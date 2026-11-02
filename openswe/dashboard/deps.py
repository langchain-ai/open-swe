"""FastAPI dependencies shared by every dashboard-mounted API router."""

from typing import Any, Protocol

from fastapi import Depends, HTTPException

from openswe.dashboard.admin import is_admin
from openswe.dashboard.oauth import require_session
from openswe.dashboard.principal import DashboardPrincipal
from openswe.dashboard.repo_access import require_repo_access_for_user


def session_is_admin(session: dict[str, Any]) -> bool:
    principal = DashboardPrincipal.from_session(session)
    # A LangSmith session's email is the LangSmith account's, which never grants admin.
    email = session.get("email") if principal.provider == "github" else None
    return principal.github_login is not None and is_admin(email, login=principal.github_login)


def require_admin(session: dict[str, Any]) -> dict[str, Any]:
    if not session_is_admin(session):
        raise HTTPException(403, "admin only")
    return session


SESSION_DEP = Depends(require_session)


def admin_session(session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    return require_admin(session)


ADMIN_DEP = Depends(admin_session)


class RepoScopedRecord(Protocol):
    @property
    def full_name(self) -> str: ...


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
