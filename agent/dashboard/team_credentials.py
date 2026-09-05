"""Encrypted team-wide LangSmith credentials for server-side read tools."""

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, field_validator

from agent.encryption import decrypt_token, encrypt_token
from agent.store import delete_value, get_value, now_iso, put_value

logger = logging.getLogger(__name__)

TEAM_CREDENTIALS_NAMESPACE: list[str] = ["team_credentials"]
LANGSMITH_KEY = "langsmith"


class LangSmithCredentialsUpdate(BaseModel):
    """Connect LangSmith with a read-scoped API key."""

    api_key: str
    endpoint: str | None = None

    @field_validator("api_key")
    @classmethod
    def _require_non_empty(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("api_key must be a non-empty string")
        return v.strip()

    @field_validator("endpoint", mode="before")
    @classmethod
    def _normalize_endpoint(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("endpoint must be a string")
        endpoint = v.strip().rstrip("/")
        return endpoint or None


@dataclass(frozen=True)
class LangSmithCredentials:
    api_key: str
    endpoint: str


DEFAULT_LANGSMITH_ENDPOINT = "https://api.smith.langchain.com"


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


async def _get_provider(key: str) -> dict[str, Any] | None:
    return await get_value(TEAM_CREDENTIALS_NAMESPACE, key)


async def _put_provider(key: str, value: dict[str, Any]) -> None:
    await put_value(TEAM_CREDENTIALS_NAMESPACE, key, value)


async def _delete_provider(key: str) -> None:
    await delete_value(TEAM_CREDENTIALS_NAMESPACE, key)


async def _provider_for_tool_loading(key: str) -> dict[str, Any] | None:
    """Read a provider record on the agent's tool-loading path.

    Fail-soft on purpose: these credentials only decide whether an optional
    integration's tools get loaded, so an unreachable store must cost a run
    those tools rather than the run itself. Dashboard reads go through
    :func:`_get_provider` and surface the failure.
    """
    try:
        return await _get_provider(key)
    except Exception:
        logger.warning("team credentials lookup failed for %s", key, exc_info=True)
        return None


async def get_team_credentials_status() -> dict[str, Any]:
    """Return a redacted, dashboard-safe view of connected providers."""
    langsmith = await _get_provider(LANGSMITH_KEY)
    return {
        "langsmith": {
            "connected": True,
            "endpoint": langsmith.get("endpoint", DEFAULT_LANGSMITH_ENDPOINT),
            "api_key_last4": langsmith.get("api_key_last4", ""),
            "updated_at": langsmith.get("updated_at"),
        }
        if langsmith
        else {"connected": False},
    }


async def connect_langsmith(update: LangSmithCredentialsUpdate) -> dict[str, Any]:
    await _put_provider(
        LANGSMITH_KEY,
        {
            "endpoint": update.endpoint or DEFAULT_LANGSMITH_ENDPOINT,
            "encrypted_api_key": encrypt_token(update.api_key),
            "api_key_last4": _last4(update.api_key),
            "updated_at": now_iso(),
        },
    )
    return await get_team_credentials_status()


async def disconnect_langsmith() -> dict[str, Any]:
    await _delete_provider(LANGSMITH_KEY)
    return await get_team_credentials_status()


async def get_langsmith_credentials() -> LangSmithCredentials | None:
    """Return decrypted LangSmith credentials, or ``None`` when not connected."""
    langsmith = await _provider_for_tool_loading(LANGSMITH_KEY)
    if not isinstance(langsmith, dict):
        return None
    api_key = decrypt_token(langsmith.get("encrypted_api_key", ""))
    if not api_key:
        return None
    return LangSmithCredentials(
        api_key=api_key,
        endpoint=langsmith.get("endpoint", DEFAULT_LANGSMITH_ENDPOINT),
    )
