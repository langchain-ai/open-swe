"""FastAPI dependencies shared by every dashboard-mounted API router."""

from typing import Protocol

from fastapi import Depends, HTTPException

from openswe.dashboard.oauth import DashboardSession, require_session
from openswe.dashboard.repo_access import require_repo_access_for_user


def require_admin(session: DashboardSession) -> DashboardSession:
    if not session.admin:
        raise HTTPException(403, "admin only")
    return session


SESSION_DEP = Depends(require_session)


def admin_session(session: DashboardSession = SESSION_DEP) -> DashboardSession:
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
