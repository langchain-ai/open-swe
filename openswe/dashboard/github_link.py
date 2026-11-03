"""Linking a GitHub account to a LangSmith sign-in, which unlocks the coding features."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from fastapi import HTTPException
from pydantic import ValidationError

from openswe.dashboard.oauth import (
    GithubUser,
    enforce_github_login_gate,
    exchange_code,
    fetch_github_user,
    issue_session,
)
from openswe.dashboard.principal import DashboardPrincipal
from openswe.dashboard.profiles import upsert_access_token_from_github_response
from openswe.encryption import decrypt_token, encrypt_token
from openswe.users import User


@dataclass(frozen=True)
class GitHubLink:
    """A GitHub account a person proved through the GitHub App's OAuth, with its tokens."""

    account: GithubUser
    email: str
    tokens: Mapping[str, object]

    @classmethod
    async def from_code(cls, code: str) -> Self:
        tokens = await exchange_code(code)
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str):
            raise HTTPException(400, "oauth exchange missing access_token")
        account, email = await fetch_github_user(access_token)
        if not account.login:
            raise HTTPException(400, "could not resolve GitHub login")
        await enforce_github_login_gate(account.login)
        return cls(account, email or "", tokens)

    def handoff_claims(self) -> dict[str, object]:
        """Claims for a desktop handoff code, whose URL a browser records: tokens stay encrypted."""
        return {
            "github": self.account.model_dump(),
            "email": self.email,
            "encrypted_tokens": encrypt_token(json.dumps(dict(self.tokens))),
        }

    @classmethod
    def from_handoff(cls, claims: Mapping[str, object]) -> Self:
        email = claims.get("email")
        encrypted = claims.get("encrypted_tokens")
        tokens = decrypt_token(encrypted) if isinstance(encrypted, str) else None
        try:
            account = GithubUser.model_validate(claims.get("github"))
            parsed: object = json.loads(tokens) if tokens else None
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, "malformed handoff code") from exc
        if not isinstance(parsed, dict) or not isinstance(email, str):
            raise HTTPException(400, "malformed handoff code")
        return cls(account, email, parsed)

    async def join(self, principal: DashboardPrincipal) -> User:
        """Join the account to the signed-in LangSmith person and keep its tokens for them."""
        if principal.provider != "langsmith":
            raise HTTPException(403, "Sign in with LangSmith to link a GitHub account")
        user = await principal.user()
        if user is None:
            raise HTTPException(401, "Open SWE account not found — sign in again")
        joined = await user.join_github(
            str(self.account.id), login=self.account.login, email=self.email
        )
        await upsert_access_token_from_github_response(
            self.account.login, self.email, dict(self.tokens)
        )
        return joined

    @staticmethod
    def session(principal: DashboardPrincipal, user: User, email: str | None) -> str:
        """The LangSmith session for ``user``, acting as their GitHub login once linked."""
        return issue_session(
            login=user.session_login,
            email=email,
            avatar_url=user.avatar_url or None,
            user_id=str(user.id),
            provider="langsmith",
            langsmith_subject=principal.langsmith_subject,
        )
