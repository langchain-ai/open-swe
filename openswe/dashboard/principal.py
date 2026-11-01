"""Provider-aware dashboard identity, independent of a GitHub handle."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException

from openswe.users import User


@dataclass(frozen=True)
class DashboardPrincipal:
    provider: Literal["github", "langsmith"]
    subject: str
    user_id: UUID | None
    langsmith_subject: str | None = None

    @classmethod
    def from_session(cls, session: Mapping[str, object]) -> DashboardPrincipal:
        provider = session.get("provider", "github")
        subject = session.get("sub")
        if (
            provider not in {"github", "langsmith"}
            or not isinstance(subject, str)
            or not subject.strip()
        ):
            raise HTTPException(401, "invalid session identity")
        raw_id = session.get("user_id")
        try:
            user_id = UUID(raw_id) if isinstance(raw_id, str) else None
        except ValueError as exc:
            if provider == "langsmith":
                raise HTTPException(401, "invalid session user") from exc
            user_id = None
        external_id = session.get("langsmith_subject")
        if provider == "langsmith" and (
            user_id is None
            or subject != f"user:{user_id}"
            or not isinstance(external_id, str)
            or not external_id
        ):
            raise HTTPException(401, "invalid LangSmith session")
        return cls(
            "langsmith" if provider == "langsmith" else "github",
            subject,
            user_id,
            external_id if isinstance(external_id, str) else None,
        )

    @property
    def github_login(self) -> str | None:
        return self.subject if self.provider == "github" else None

    def authorize_path(self, path: str, method: str) -> None:
        if self.provider == "github":
            return
        prefix = "/dashboard/api/"
        relative = path.removeprefix(prefix).rstrip("/") if path.startswith(prefix) else ""
        if relative == "my-credentials/langsmith" and method in {"GET", "DELETE"}:
            return
        if relative in {"me", "auth/logout"}:
            return
        raise HTTPException(403, "This operation requires a GitHub account")

    async def user(self) -> User | None:
        if self.provider == "github":
            return await User.for_session(self.user_id, self.subject)
        user = await User.get(self.user_id) if self.user_id is not None else None
        if user is None or not any(
            identity.provider == "langsmith" and identity.external_id == self.langsmith_subject
            for identity in user.identities
        ):
            raise HTTPException(401, "LangSmith account not found — sign in again")
        return user

    async def authorize(self) -> None:
        if self.provider == "github":
            return
        from openswe.dashboard.langsmith_login import LangSmithLogin

        await self.user()
        await LangSmithLogin().authorize_session(self)
