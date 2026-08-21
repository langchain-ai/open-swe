"""The gates every dashboard endpoint hangs off, as FastAPI dependencies.

A signed-in session, an admin (by cookie or by CI credential), and access to
the repository named in the path. Expressing repo access as a dependency is
what keeps it from being an ``await`` a new endpoint can forget to write.
"""

from typing import Any, NamedTuple

from fastapi import Depends, HTTPException, Request

from .admin import is_admin
from .github_token_auth import admin_session_for_github_token, bearer_github_token
from .oauth import require_session
from .oidc_auth import admin_session_for_actions_oidc, is_actions_oidc_token
from .repo_access import require_repo_access_for_user
from .review_styles import normalize_repo_full_name

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
