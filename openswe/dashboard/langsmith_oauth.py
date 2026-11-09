"""A person's LangSmith access, for any feature that calls LangSmith as them.

LangSmith is the only sign-in, so its login grant (``openswe.dashboard.langsmith_login``)
is the credential; ``langsmith_access_token`` is the entry point for callers.
"""

from typing import TYPE_CHECKING, Self
from uuid import UUID

from fastapi import HTTPException
from runtime_api.gateway_auth import GatewayBearer
from runtime_api.loop import GatewayCredential
from runtime_api.models import gateway_api_key

from openswe.agent_api.connections import SENDER_USER_KEY
from openswe.config import ENV
from openswe.users.models import User

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig


class LangSmithOAuthError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def langsmith_issuer() -> str:
    return ENV.LANGSMITH_CONNECTION_URL.get().rstrip("/")


def langsmith_oauth_configured() -> bool:
    """Whether the LangSmith OAuth client credentials are set."""
    return ENV.LANGSMITH_OAUTH_CLIENT_ID.is_set() and ENV.LANGSMITH_OAUTH_CLIENT_SECRET.is_set()


async def langsmith_access_token(login: str) -> str | None:
    """The person's current LangSmith login token, refreshed when near expiry.

    ``None`` when they must sign in with LangSmith again; a temporary failure raises
    :class:`LangSmithOAuthError`.
    """
    if not langsmith_oauth_configured():
        return None
    from openswe.dashboard.langsmith_login import LangSmithLogin

    try:
        return await LangSmithLogin().access_token(login)
    except HTTPException as exc:
        if exc.status_code in {401, 403}:
            return None
        raise LangSmithOAuthError(exc.status_code, str(exc.detail)) from exc


class LangSmithSignInRequired(RuntimeError):
    """A run would call models as a person whose LangSmith login can no longer be used."""


class LangSmithModelAccess:
    """Whose LangSmith credential a run's model calls carry through the LLM gateway.

    A run a person started with their own message (``SENDER_USER_KEY``: from the
    dashboard, or as a channel's verified sender) calls models as them, on their LangSmith
    login, so the usage lands on the workspace they signed in to and under its spend
    limits. Any other run (a webhook, an automation, a wakeup, a system thread) uses the
    deployment's own key.
    """

    SIGN_IN_AGAIN = (
        "Robin calls models as you, through your LangSmith login, and that login has "
        "expired or was revoked. Sign in to Robin again, then retry."
    )

    def __init__(self, user_id: UUID | None) -> None:
        self.user_id = user_id

    @classmethod
    def for_run(cls, config: RunnableConfig) -> Self:
        user_id = (config.get("metadata") or {}).get(SENDER_USER_KEY)
        if not isinstance(user_id, str) or not langsmith_oauth_configured():
            return cls(None)
        return cls(User.key_id(f"user:{user_id}"))

    async def credential(self) -> GatewayCredential:
        """What the run authenticates to the gateway with; a temporary failure raises."""
        if self.user_id is None:
            return GatewayCredential()
        token = await langsmith_access_token(f"user:{self.user_id}")
        if token is None:
            return GatewayCredential(unavailable=self.SIGN_IN_AGAIN)
        return GatewayCredential(token=token)

    async def token(self) -> str:
        """The bearer for one gateway request the backend itself makes for the run."""
        credential = await self.credential()
        if credential.unavailable is not None:
            raise LangSmithSignInRequired(credential.unavailable)
        if credential.token is not None:
            return credential.token
        key = gateway_api_key()
        if key is None:
            raise RuntimeError("No LANGSMITH_GATEWAY_API_KEY or LANGSMITH_API_KEY is set")
        return key

    def bearer(self) -> GatewayBearer:
        return GatewayBearer(self.token)
