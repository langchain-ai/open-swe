"""A person's LangSmith access, for any feature that calls LangSmith as them.

LangSmith is the only sign-in, so its login grant (``openswe.dashboard.langsmith_login``)
is the credential; ``langsmith_access_token`` is the entry point for callers.
"""

from fastapi import HTTPException

from openswe.config import ENV
from openswe.database import postgres


class LangSmithOAuthError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def langsmith_issuer() -> str:
    return ENV.LANGSMITH_CONNECTION_URL.get().rstrip("/")


def langsmith_oauth_configured() -> bool:
    """Grants live in PostgreSQL, so LangSmith access needs it alongside the client."""
    return (
        ENV.LANGSMITH_OAUTH_CLIENT_ID.is_set()
        and ENV.LANGSMITH_OAUTH_CLIENT_SECRET.is_set()
        and postgres.configured()
    )


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
