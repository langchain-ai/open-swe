"""The GitHub identity carried by a person's thread operations."""

from dataclasses import dataclass

from openswe.dashboard.admin import is_admin


@dataclass(frozen=True)
class ThreadIdentity:
    subject: str

    @property
    def github_login(self) -> str:
        return self.subject

    @property
    def sender_id(self) -> str:
        return f"github:{self.github_login}"

    def is_admin(self, email: str | None = None) -> bool:
        return is_admin(email, login=self.github_login)
