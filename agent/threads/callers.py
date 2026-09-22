"""Whoever is asking to start or read a thread, and what they are allowed to ask for.

Three kinds of caller reach the thread API. A person signs in and gets their own
threads. A workspace API key and a federated GitHub Actions workflow are
machines: they start threads that belong to a workspace rather than to anyone,
and they may read back only what they themselves started, because none of the
dashboard's ownership rules describe them.

Which kind of thread a request creates is never inferred from the caller. The
request names it, and the caller is checked against it:

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

from agent.api_keys.deps import api_key_from_token
from agent.api_keys.models import ApiKey
from agent.dashboard.admin import is_admin
from agent.dashboard.oauth import optional_session
from agent.federation.github_oidc import (
    GitHubActionsClaims,
    InvalidFederatedToken,
    looks_federated,
    verify,
)
from agent.threads.summary import _assert_thread_postable, assert_thread_readable
from agent.utils.json_types import JsonObject
from agent.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

CallerKind = Literal["api_key", "github_actions", "person"]
ThreadType = Literal["system", "workspace", "private"]

STARTED_BY_ID = "started_by_id"
STARTED_BY_NAME = "started_by_name"

_UNAUTHENTICATED = "authenticate with an API key, a federated workflow token, or a session"


@dataclass(frozen=True, kw_only=True)
class Caller:
    """One authenticated caller. Machines carry a workspace; people carry a login."""

    kind: CallerKind
    workspace: str = ""
    login: str | None = None
    email: str | None = None
    admin: bool = False
    started_by_id: str = ""
    started_by_name: str = ""
    created_by: str = ""
    # A workflow names no repository when it means its own.
    default_repo: str = ""

    @classmethod
    def of_key(cls, key: ApiKey) -> Caller:
        return cls(
            kind="api_key",
            workspace=key.workspace,
            started_by_id=f"api_key:{key.id}",
            started_by_name=key.name,
            created_by=key.created_by,
        )

    @classmethod
    def of_workflow(cls, claims: GitHubActionsClaims, workspace: str) -> Caller:
        return cls(
            kind="github_actions",
            workspace=workspace,
            started_by_id=f"github_actions:{claims.repository.lower()}",
            started_by_name=f"{claims.repository} ({claims.workflow or 'workflow'})",
            created_by=claims.repository,
            default_repo=claims.repository,
        )

    @classmethod
    def of_login(cls, login: str, email: str | None = None) -> Caller:
        return cls(
            kind="person",
            login=login,
            email=email,
            admin=is_admin(email, login=login),
        )

    @classmethod
    def of_person(cls, session: dict[str, Any]) -> Caller:
        login = session.get("sub")
        if not isinstance(login, str) or not login.strip():
            raise HTTPException(401, "session carries no user")
        email = session.get("email")
        return cls.of_login(login.strip(), email if isinstance(email, str) else None)

    @property
    def machine(self) -> bool:
        return self.kind != "person"

    @property
    def person(self) -> str:
        if self.login is None:
            raise RuntimeError("caller is a machine, not a person")
        return self.login

    @property
    def sender_id(self) -> str:
        """The identity a run records as having sent its opening message."""
        if self.machine:
            return f"system:{self.started_by_id.replace(':', '/')}"
        return f"github:{self.person}"

    def authorize(self, requested: ThreadType) -> None:
        """Refuse a kind of thread this caller may not create."""
        if self.machine and requested != "system":
            raise HTTPException(403, "a machine caller may only start system threads")
        if not self.machine and requested == "system" and not self.admin:
            raise HTTPException(403, "only admins may start system threads")

    def assert_can_read(self, metadata: JsonObject) -> None:
        if self.machine:
            self._assert_started_it(metadata)
            return
        assert_thread_readable(metadata, self.login, self.email)

    def assert_can_post(self, metadata: JsonObject) -> None:
        if self.machine:
            self._assert_started_it(metadata)
            return
        _assert_thread_postable(metadata, self.person, self.email)

    def _assert_started_it(self, metadata: JsonObject) -> None:
        # Deliberately not "is this thread in my workspace": a key is not an
        # owner of everything its workspace runs, and 404 rather than 403 keeps
        # it from discovering which thread ids exist.
        if metadata.get(STARTED_BY_ID) != self.started_by_id:
            raise HTTPException(404, "thread not found")


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return token.strip() if scheme.strip().lower() == "bearer" else ""


async def _federated_caller(token: str) -> Caller:
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
    return Caller.of_workflow(claims, workspace)


async def require_caller(request: Request) -> Caller:
    """The caller behind this request, whichever credential it brought.

    A bearer token is matched against the API keys first and only then checked
    as GitHub's, so nothing is verified against keys it was not minted for.
    """
    token = _bearer(request)
    if token:
        key = await api_key_from_token(token)
        if key is not None:
            return Caller.of_key(key)
        if looks_federated(token):
            return await _federated_caller(token)
    session = optional_session(request)
    if session is None:
        raise HTTPException(401, _UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})
    return Caller.of_person(session)


CALLER_DEP = Depends(require_caller)
CallerDep = Annotated[Caller, CALLER_DEP]
