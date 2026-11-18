"""Whoever is asking to start or read a thread, and what they are allowed to ask for.

Three kinds of principal reach the thread API. A person signs in, or presents a
LangSmith personal access token, and gets their own threads. The bot (a
LangSmith service key with ``workspaces:manage`` in Robin's LangSmith workspace)
and a federated GitHub
Actions workflow are machines: they start threads that belong to a workspace
rather than to anyone, and they may read back only what they themselves started,
because none of the dashboard's ownership rules describe them.

Which kind of thread a request creates is never inferred from who asked. The
request names it, and the principal is checked against it:

======================  ==============================================
``system``              machines, and admins
``workspace``           any person
``private``             any person
======================  ==============================================
"""

import logging
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Request

from openswe.audit_logs.middleware import bind_actor
from openswe.audit_logs.models import AuditLogEnrichments
from openswe.dashboard.langsmith_api_keys import LANGSMITH_API_KEYS, LangSmithApiKey
from openswe.dashboard.oauth import bind_audit_session, optional_session
from openswe.dashboard.principal import DashboardPrincipal
from openswe.federation.github_oidc import (
    GitHubActionsClaims,
    InvalidFederatedToken,
    looks_federated,
    verify,
)
from openswe.threads.identity import ThreadIdentity
from openswe.threads.summary import _assert_thread_postable, assert_thread_readable
from openswe.users.models import User
from openswe.utils.json_types import JsonObject
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

PrincipalKind = Literal["bot", "github_actions", "person"]
ThreadType = Literal["system", "workspace", "private"]

STARTED_BY_ID = "started_by_id"
STARTED_BY_NAME = "started_by_name"

_UNAUTHENTICATED = "authenticate with a LangSmith API key, a federated workflow token, or a session"
_PAT_NEEDS_SIGN_IN = (
    "Sign in to Robin with LangSmith before using a personal access token: its runs "
    "call models through your LangSmith login"
)
BOT_NAME = "Robin bot"


@dataclass(frozen=True, kw_only=True)
class Principal:
    """One authenticated principal. Machines carry a workspace; people carry a login."""

    kind: PrincipalKind
    workspace: str = ""
    login: str | None = None
    email: str | None = None
    admin: bool = False
    started_by_id: str = ""
    started_by_name: str = ""
    created_by: str = ""
    # A workflow names no repository when it means its own.
    default_repo: str = ""
    # The repositories the threads it starts may reach; None is the installation.
    token_repositories: tuple[str, ...] | None = None

    @classmethod
    def of_bot(cls, key: LangSmithApiKey, workspace: str = DEFAULT_WORKSPACE_SLUG) -> Principal:
        """The workspace's bot. Every service key is the same bot, so a rotated key
        still reads the threads the old one started."""
        return cls(
            kind="bot",
            workspace=workspace,
            started_by_id=f"bot:{key.workspace_id}",
            started_by_name=BOT_NAME,
            created_by="bot",
        )

    @classmethod
    async def of_langsmith_key(cls, request: Request, key: LangSmithApiKey) -> Principal:
        """A service key is the bot; a personal access token is the Robin user behind it.

        The token gets exactly what that person's session would: the same path
        rules, and the same live check of their LangSmith login, which their runs
        call models through.
        """
        if key.kind == "service":
            principal = cls.of_bot(key)
            bind_actor(
                request,
                api_key_id=key.audit_id,
                enrichments=AuditLogEnrichments(
                    actor_kind="api_key",
                    actor_login=principal.started_by_id,
                    workspace=principal.workspace,
                ),
            )
            return principal
        user = await User.for_identity("langsmith", key.ls_user_id)
        if user is None:
            raise HTTPException(403, _PAT_NEEDS_SIGN_IN)
        dashboard = DashboardPrincipal("langsmith", user.session_login, user.id, key.ls_user_id)
        if request.url.path.startswith("/dashboard/api/"):
            dashboard.authorize_path(request.url.path, request.method)
        try:
            await dashboard.authorize()
        except HTTPException as exc:
            # A 401 would read as a bad key; the key is fine, the login behind it is not.
            if exc.status_code == 401:
                raise HTTPException(403, _PAT_NEEDS_SIGN_IN) from exc
            raise
        bind_actor(
            request,
            user_id=user.id,
            api_key_id=key.audit_id,
            enrichments=AuditLogEnrichments(actor_kind="person", actor_login=user.session_login),
        )
        return cls.of_login(user.session_login, key.email or user.email or None)

    @classmethod
    def of_workflow(cls, claims: GitHubActionsClaims, workspace: str) -> Principal:
        return cls(
            kind="github_actions",
            workspace=workspace,
            started_by_id=f"github_actions:{claims.repository.lower()}",
            started_by_name=f"{claims.repository} ({claims.workflow or 'workflow'})",
            created_by=claims.repository,
            default_repo=claims.repository,
            # Anyone can open a pull request against a public repository, and a
            # workflow it triggers must not reach the rest of the installation.
            token_repositories=(
                None
                if claims.repository_visibility in ("private", "internal")
                else (claims.repository,)
            ),
        )

    @classmethod
    def of_login(cls, login: str, email: str | None = None) -> Principal:
        return cls(
            kind="person",
            login=login,
            email=email,
            admin=ThreadIdentity(login).is_admin(email),
        )

    @classmethod
    def of_person(cls, session: dict[str, Any]) -> Principal:
        principal = DashboardPrincipal.from_session(session)
        email = session.get("email")
        return cls.of_login(principal.subject.strip(), email if isinstance(email, str) else None)

    @property
    def machine(self) -> bool:
        return self.kind != "person"

    @property
    def person(self) -> str:
        if self.login is None:
            raise RuntimeError("principal is a machine, not a person")
        return self.login

    @property
    def sender_id(self) -> str:
        """The identity a run records as having sent its opening message."""
        if self.machine:
            return f"system:{self.started_by_id.replace(':', '/')}"
        return ThreadIdentity(self.person).sender_id

    def authorize(self, requested: ThreadType) -> None:
        """Refuse a kind of thread this principal may not create."""
        if self.machine and requested != "system":
            raise HTTPException(403, "a machine principal may only start system threads")
        if not self.machine and requested == "system" and not self.admin:
            raise HTTPException(403, "only admins may start system threads")

    def assert_can_read(self, metadata: JsonObject) -> None:
        if self.machine:
            self._assert_started_it(metadata)
            return
        assert_thread_readable(metadata, self.login, self.email)

    async def assert_can_post(self, metadata: JsonObject) -> None:
        if self.machine:
            self._assert_started_it(metadata)
            return
        await _assert_thread_postable(metadata, self.person, self.email)

    def _assert_started_it(self, metadata: JsonObject) -> None:
        # Deliberately not "is this thread in my workspace": a key is not an
        # owner of everything its workspace runs, and 404 rather than 403 keeps
        # it from discovering which thread ids exist.
        if metadata.get(STARTED_BY_ID) != self.started_by_id:
            raise HTTPException(404, "thread not found")


def _bearer(request: Request) -> str:
    """The presented token: ``Authorization: Bearer``, else LangSmith's ``X-Api-Key``."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.strip().lower() == "bearer" and token.strip():
        return token.strip()
    return request.headers.get("X-Api-Key", "").strip()


async def _federated_principal(token: str) -> Principal:
    """The workflow behind a GitHub token, if a workspace lets that repository in."""
    try:
        claims = await verify(token)
    except InvalidFederatedToken as exc:
        logger.info("Rejected a federated GitHub Actions token", extra={"reason": str(exc)})
        raise HTTPException(
            401, "invalid federated token", headers={"WWW-Authenticate": "Bearer"}
        ) from exc
    workspace = await WORKSPACES.thread_starter_of_repo(claims.repository)
    if workspace is None:
        logger.info(
            "A verified workflow is not allowed to start threads",
            extra={"repository": claims.repository, "workflow_ref": claims.workflow_ref},
        )
        raise HTTPException(403, "this repository may not start threads in any workspace")
    logger.info(
        "A federated workflow authenticated",
        extra={
            "repository": claims.repository,
            "workflow_ref": claims.workflow_ref,
            "workspace": workspace,
        },
    )
    return Principal.of_workflow(claims, workspace)


async def require_principal(request: Request) -> Principal:
    """The principal behind this request, whichever credential it brought.

    A token is sent to LangSmith only when it is shaped like a LangSmith key, and
    to GitHub's verifier only when it is shaped like GitHub's, so no credential is
    shown to an issuer that did not mint it.
    """
    token = _bearer(request)
    if token:
        key = await LANGSMITH_API_KEYS.verify(token)
        if key is not None:
            return await Principal.of_langsmith_key(request, key)
        if looks_federated(token):
            principal = await _federated_principal(token)
            bind_actor(
                request,
                enrichments=AuditLogEnrichments(
                    actor_kind="github_actions",
                    actor_login=principal.started_by_id,
                    workspace=principal.workspace,
                ),
            )
            return principal
    session = optional_session(request)
    if session is None:
        raise HTTPException(401, _UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})
    from openswe.dashboard.principal import DashboardPrincipal

    dashboard = DashboardPrincipal.from_session(session)
    if request.url.path.startswith("/dashboard/api/"):
        dashboard.authorize_path(request.url.path, request.method)
    await dashboard.authorize()
    principal = Principal.of_person(session)
    bind_audit_session(request, session)
    return principal


PRINCIPAL_DEP = Depends(require_principal)
PrincipalDep = Annotated[Principal, PRINCIPAL_DEP]
