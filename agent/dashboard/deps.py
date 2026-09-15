"""FastAPI dependencies shared by every dashboard-mounted API router."""

from typing import Any, Protocol

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent.dashboard.admin import is_admin
from agent.dashboard.oauth import SESSION_COOKIE, require_session
from agent.dashboard.oidc_auth import admin_session_for_actions_oidc, is_actions_oidc_token
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.github.token_auth import admin_session_for_github_token, bearer_github_token


def session_is_admin(session: dict[str, Any]) -> bool:
    return is_admin(session.get("email"), login=session.get("sub"))


def require_admin(session: dict[str, Any]) -> dict[str, Any]:
    if not session_is_admin(session):
        raise HTTPException(403, "admin only")
    return session


SESSION_DEP = Depends(require_session)


def admin_session(session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    return require_admin(session)


ADMIN_DEP = Depends(admin_session)
_ADMIN_BEARER_DEP = Depends(
    HTTPBearer(
        scheme_name="AdminBearer",
        description="An admin's GitHub user token or an allowlisted GitHub Actions OIDC token.",
        auto_error=False,
    )
)


async def admin_session_or_ci_token(
    request: Request,
    _cookie: str | None = Depends(SESSION_COOKIE),
    _bearer: HTTPAuthorizationCredentials | None = _ADMIN_BEARER_DEP,
) -> dict[str, Any]:
    """Admin gate that also accepts CI credentials: an Actions OIDC token, or an
    admin's GitHub personal access token."""
    token = bearer_github_token(request)
    if token:
        if is_actions_oidc_token(token):
            return await admin_session_for_actions_oidc(token)
        return await admin_session_for_github_token(token)
    return require_admin(require_session(request))


ADMIN_OR_TOKEN_DEP = Depends(admin_session_or_ci_token)


async def filter_repo_records_for_user(
    login: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        full_name = record.get("full_name")
        if not isinstance(full_name, str):
            continue
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        out.append(record)
    return out


class RepoScopedRecord(Protocol):
    full_name: str


async def filter_repo_models_for_user[RepoRecordT: RepoScopedRecord](
    login: str,
    records: list[RepoRecordT],
) -> list[RepoRecordT]:
    out: list[RepoRecordT] = []
    for record in records:
        try:
            await require_repo_access_for_user(login, record.full_name)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        out.append(record)
    return out
