"""FastAPI dependencies shared by every dashboard-mounted API router."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from itertools import batched
from typing import Annotated, Any, Protocol

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from openswe.dashboard.admin import is_admin
from openswe.dashboard.authentication import DashboardAuthentication
from openswe.dashboard.oauth import SESSION_COOKIE, bind_audit_session, require_session
from openswe.dashboard.principal import AuthorizedClaims, DashboardPrincipal
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.users import User


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
    for batch in batched(records, 4, strict=False):
        results = await asyncio.gather(
            *(require_repo_access_for_user(login, record.full_name) for record in batch),
            return_exceptions=True,
        )
        for record, result in zip(batch, results, strict=True):
            if isinstance(result, HTTPException) and result.status_code in {403, 404}:
                continue
            if isinstance(result, BaseException):
                raise result
            out.append(record)
    return out


@dataclass
class DashboardRead:
    """An authenticated person and the request's short-lived database phase."""

    principal: DashboardPrincipal
    user: User
    claims: AuthorizedClaims
    email: str | None
    session: AsyncSession
    scope: AsyncExitStack

    async def close(self) -> None:
        await self.scope.aclose()

    @classmethod
    async def open(
        cls, request: HTTPConnection, _cookie: str | None = Depends(SESSION_COOKIE)
    ) -> AsyncIterator[DashboardRead]:
        async with AsyncExitStack() as scope:
            session, claims = await scope.enter_async_context(
                DashboardAuthentication(request).authorized_person()
            )
            principal = DashboardPrincipal.from_session(claims)
            bind_audit_session(request, claims)
            email = claims.get("email")
            yield cls(
                principal,
                claims.user,
                claims,
                email if isinstance(email, str) else None,
                session,
                scope,
            )


DashboardReadDep = Annotated[DashboardRead, Depends(DashboardRead.open, scope="function")]
