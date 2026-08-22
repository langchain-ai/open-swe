"""The gates every dashboard endpoint hangs off.

Two kinds of gate live here, and nowhere else. Request-scoped ones — a signed-in
session, an admin (by cookie or by CI credential), access to the repository
named in the path — are FastAPI dependencies, so an endpoint cannot forget to
``await`` them. Thread-scoped ones are predicates over a thread's metadata:
who owns a thread, who may read it, who may post into it. Every module that
touches a thread asks *these* functions, so the owner rule (verified login, or
the triggering email) has exactly one definition and cannot drift per feature.
"""

from collections.abc import Mapping
from typing import Any, NamedTuple

from fastapi import Depends, HTTPException, Request

from ..config import langgraph_client
from ..settings.admin import is_admin
from ..settings.review_styles import normalize_repo_full_name
from ..utils.json_types import JsonObject, ThreadLike, thread_metadata
from .github_token_auth import admin_session_for_github_token, bearer_github_token
from .oauth import require_session
from .oidc_auth import admin_session_for_actions_oidc, is_actions_oidc_token
from .repo_access import require_repo_access_for_user

SESSION = Depends(require_session)


def session_is_admin(session: dict[str, Any]) -> bool:
    return is_admin(session.get("email"), login=session.get("sub"))


def require_admin(session: dict[str, Any]) -> dict[str, Any]:
    if not session_is_admin(session):
        raise HTTPException(403, "admin only")
    return session


def admin_session(session: dict[str, Any] = SESSION) -> dict[str, Any]:
    return require_admin(session)


ADMIN = Depends(admin_session)


async def admin_session_or_ci_token(request: Request) -> dict[str, Any]:
    """Admin gate that also accepts CI credentials: an Actions OIDC token, or an
    admin's GitHub personal access token."""
    token = bearer_github_token(request)
    if token:
        if is_actions_oidc_token(token):
            return await admin_session_for_actions_oidc(token)
        return await admin_session_for_github_token(token)
    return require_admin(require_session(request))


ADMIN_OR_CI_TOKEN = Depends(admin_session_or_ci_token)


class RepoAccess(NamedTuple):
    """A repository the caller has proven access to, and the token that proved it.

    Endpoints that go on to call GitHub as the user reuse ``token`` instead of
    re-resolving one: it is the token the access check just succeeded with.
    """

    full_name: str
    token: str


async def require_repo_access(
    owner: str, repo: str, session: dict[str, Any] = SESSION
) -> RepoAccess:
    full_name = normalize_repo_full_name(f"{owner}/{repo}")
    return RepoAccess(full_name, await require_repo_access_for_user(session["sub"], full_name))


REPO_ACCESS = Depends(require_repo_access)


async def require_repo_full_name_access(
    full_name: str, session: dict[str, Any] = SESSION
) -> RepoAccess:
    normalized = normalize_repo_full_name(full_name)
    return RepoAccess(normalized, await require_repo_access_for_user(session["sub"], normalized))


REPO_FULL_NAME_ACCESS = Depends(require_repo_full_name_access)


DASHBOARD_SOURCE = "dashboard"
# Sources whose threads surface in the Agents UI. A thread from anywhere else
# (a reviewer thread, a PR chat) is not a dashboard thread and is invisible here.
SURFACED_THREAD_SOURCES: frozenset[str] = frozenset(
    {DASHBOARD_SOURCE, "github", "slack", "linear", "schedule"}
)


def thread_source(metadata: Mapping[str, Any]) -> str:
    source = metadata.get("source")
    return source if isinstance(source, str) and source else DASHBOARD_SOURCE


def thread_owner_login(metadata: Mapping[str, Any]) -> str | None:
    login = metadata.get("github_login")
    return login.strip() if isinstance(login, str) and login.strip() else None


def thread_owner_email(metadata: Mapping[str, Any]) -> str | None:
    email = metadata.get("triggering_user_email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def thread_identifies_user(
    metadata: Mapping[str, Any], login: str, email: str | None = None
) -> bool:
    """Whether the thread's recorded owner is this caller.

    Both halves are load-bearing: a Slack- or Linear-triggered thread often
    knows only the triggering email, and a dashboard thread only the login, so
    matching on either is what lets the same person reach their own thread
    whichever surface started it.
    """
    if thread_owner_login(metadata) == login:
        return True
    return bool(email) and thread_owner_email(metadata) == email.strip().lower()


def user_owns_thread(metadata: Mapping[str, Any], login: str, email: str | None = None) -> bool:
    return thread_source(metadata) in SURFACED_THREAD_SOURCES and thread_identifies_user(
        metadata, login, email
    )


def thread_is_readable(metadata: Mapping[str, Any]) -> bool:
    """Any surfaced-source thread is readable by authenticated users.

    Dashboard login is already gated by ``ALLOWED_GITHUB_ORGS`` (see
    ``oauth.enforce_org_login_gate``), so any logged-in user is a trusted
    org member. This lets teammates open "Open in Web" links shared in Slack
    threads with read-only access.
    """
    return thread_source(metadata) in SURFACED_THREAD_SOURCES


def assert_thread_owner(metadata: Mapping[str, Any], login: str, email: str | None = None) -> None:
    if not user_owns_thread(metadata, login, email):
        raise HTTPException(404, "thread not found")


def assert_thread_readable(metadata: Mapping[str, Any]) -> None:
    if not thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")


def assert_thread_postable(
    metadata: Mapping[str, Any], login: str, email: str | None = None
) -> None:
    assert_thread_readable(metadata)
    if metadata.get("admin_thread") is True and not is_admin(email, login=login):
        raise HTTPException(403, "only admins can send messages in admin threads")


async def get_thread(thread_id: str) -> ThreadLike:
    """The thread, or 404. The one place the dashboard reads a thread by id.

    Any read failure is a 404 rather than a 502: a thread the dashboard cannot
    confirm must not be treated as one the caller may see.
    """
    try:
        return await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc


async def get_thread_metadata(thread_id: str) -> JsonObject:
    return thread_metadata(await get_thread(thread_id))


async def get_owned_thread(thread_id: str, login: str, *, email: str | None = None) -> ThreadLike:
    thread = await get_thread(thread_id)
    assert_thread_owner(thread_metadata(thread), login, email)
    return thread


async def get_owned_thread_metadata(
    thread_id: str, login: str, *, email: str | None = None
) -> JsonObject:
    return thread_metadata(await get_owned_thread(thread_id, login, email=email))


async def get_readable_thread(thread_id: str) -> ThreadLike:
    thread = await get_thread(thread_id)
    assert_thread_readable(thread_metadata(thread))
    return thread


async def get_readable_thread_metadata(thread_id: str) -> JsonObject:
    return thread_metadata(await get_readable_thread(thread_id))
