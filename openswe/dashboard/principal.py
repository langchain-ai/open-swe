"""The GitHub identity carried by a dashboard session."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from openswe.users import User


@dataclass(frozen=True)
class DashboardPrincipal:
    provider: Literal["github"]
    subject: str
    user_id: UUID | None

    @classmethod
    def from_session(cls, session: Mapping[str, object]) -> DashboardPrincipal:
        provider = session.get("provider", "github")
        subject = session.get("sub")
        if provider != "github" or not isinstance(subject, str) or not subject.strip():
            raise HTTPException(401, "invalid session identity")
        raw_id = session.get("user_id")
        try:
            user_id = UUID(raw_id) if isinstance(raw_id, str) else None
        except ValueError:
            user_id = None
        return cls("github", subject, user_id)

    @property
    def github_login(self) -> str:
        return self.subject

    async def user(self) -> User | None:
        return await User.for_session(self.user_id, self.github_login)
