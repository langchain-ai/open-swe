"""Provider-independent identity carried by a person's thread operations."""

from dataclasses import dataclass
from uuid import UUID

from openswe.input_messages import PersonIdentity
from openswe.users import User


@dataclass(frozen=True)
class ThreadIdentity:
    subject: str

    @property
    def user_id(self) -> UUID | None:
        if not self.subject.startswith("user:"):
            return None
        return UUID(self.subject.removeprefix("user:"))

    @property
    def github_login(self) -> str | None:
        return None if self.user_id is not None else self.subject

    @property
    def sender_id(self) -> str:
        return self.subject if self.user_id is not None else f"github:{self.subject}"

    @property
    def person(self) -> PersonIdentity:
        person: PersonIdentity = {"id": self.sender_id}
        if self.github_login:
            person["github_login"] = self.github_login
        return person

    def is_admin(self, email: str | None = None) -> bool:
        from openswe.dashboard.admin import is_admin

        return self.github_login is not None and is_admin(email, login=self.subject)

    async def authorize(self) -> None:
        if self.user_id is None:
            return
        from fastapi import HTTPException

        from openswe.dashboard.langsmith_login import LangSmithLogin

        if await LangSmithLogin().access_token(self.subject) is None:
            raise HTTPException(401, "LangSmith login expired — sign in again")

    async def user(self) -> User | None:
        if self.user_id is not None:
            return await User.get(self.user_id)
        return await User.for_login("github", self.subject)
