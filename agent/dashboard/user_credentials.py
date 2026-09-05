"""Per-user third-party service credentials."""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from agent.dashboard.team_credentials import DEFAULT_LANGSMITH_ENDPOINT, LangSmithCredentials
from agent.encryption import decrypt_token, encrypt_token
from agent.store import delete_value, get_value, now_iso, put_value

logger = logging.getLogger(__name__)

USER_CREDENTIALS_NAMESPACE: list[str] = ["user_credentials"]
CURRENTS_KEY = "currents"
LANGSMITH_KEY = "langsmith"

CURRENTS_API_BASE = "https://api.currents.dev/v1"


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


def _namespace(login: str) -> list[str]:
    return [*USER_CREDENTIALS_NAMESPACE, login]


async def _get_provider(login: str, key: str) -> dict[str, Any] | None:
    return await get_value(_namespace(login), key)


async def _put_provider(login: str, key: str, value: dict[str, Any]) -> None:
    await put_value(_namespace(login), key, value)


async def _delete_provider(login: str, key: str) -> None:
    await delete_value(_namespace(login), key)


async def _provider_for_tool_loading(login: str, key: str) -> dict[str, Any] | None:
    """Read a provider record on the agent's tool-loading path.

    Fail-soft on purpose: these credentials only decide whether an optional
    integration's tools get loaded, so an unreachable store must cost a run
    those tools rather than the run itself. Dashboard reads go through
    :func:`_get_provider` and surface the failure.
    """
    try:
        return await _get_provider(login, key)
    except Exception:
        logger.warning("user credentials lookup failed for %s/%s", login, key, exc_info=True)
        return None


class CurrentsCredentialsUpdate(BaseModel):
    """Connect Currents.dev with an organization API key."""

    api_key: str

    @field_validator("api_key")
    @classmethod
    def _require_non_empty(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("api_key must be a non-empty string")
        return v.strip()


class UserLangSmithCredentialsUpdate(CurrentsCredentialsUpdate):
    """Connect LangSmith with an API key."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("api_key")
    @classmethod
    def _require_redactable(cls, value: str) -> str:
        if len(value) < 5:
            raise ValueError("api_key must be at least 5 characters")
        return value


async def get_currents_status(login: str) -> dict[str, Any]:
    """Return a redacted, dashboard-safe view of the user's Currents key."""
    currents = await _get_provider(login, CURRENTS_KEY)
    return {
        "currents": {
            "connected": True,
            "api_key_last4": currents.get("api_key_last4", ""),
            "updated_at": currents.get("updated_at"),
        }
        if currents
        else {"connected": False},
    }


async def connect_currents(login: str, update: CurrentsCredentialsUpdate) -> dict[str, Any]:
    await _put_provider(
        login,
        CURRENTS_KEY,
        {
            "encrypted_api_key": encrypt_token(update.api_key),
            "api_key_last4": _last4(update.api_key),
            "updated_at": now_iso(),
        },
    )
    return await get_currents_status(login)


async def disconnect_currents(login: str) -> dict[str, Any]:
    await _delete_provider(login, CURRENTS_KEY)
    return await get_currents_status(login)


async def get_currents_api_key(login: str) -> str | None:
    """Return the decrypted Currents API key, or ``None`` when not connected."""
    currents = await _provider_for_tool_loading(login, CURRENTS_KEY)
    if not isinstance(currents, dict):
        return None
    api_key = decrypt_token(currents.get("encrypted_api_key", ""))
    return api_key or None


async def get_langsmith_status(login: str) -> dict[str, Any]:
    """Return a redacted, dashboard-safe view of the user's LangSmith key."""
    langsmith = await _get_provider(login, LANGSMITH_KEY)
    return {
        "langsmith": {
            "connected": True,
            "api_key_last4": langsmith.get("api_key_last4", ""),
            "updated_at": langsmith.get("updated_at"),
        }
        if langsmith
        else {"connected": False},
    }


async def connect_langsmith(login: str, update: UserLangSmithCredentialsUpdate) -> dict[str, Any]:
    await _put_provider(
        login,
        LANGSMITH_KEY,
        {
            "encrypted_api_key": encrypt_token(update.api_key),
            "api_key_last4": _last4(update.api_key),
            "updated_at": now_iso(),
        },
    )
    return await get_langsmith_status(login)


async def disconnect_langsmith(login: str) -> dict[str, Any]:
    await _delete_provider(login, LANGSMITH_KEY)
    return await get_langsmith_status(login)


def _langsmith_credentials(record: object) -> LangSmithCredentials | None:
    if not isinstance(record, dict):
        return None
    api_key = decrypt_token(record.get("encrypted_api_key", ""))
    if not api_key:
        return None
    return LangSmithCredentials(api_key=api_key, endpoint=DEFAULT_LANGSMITH_ENDPOINT)


async def get_langsmith_credentials(login: str) -> LangSmithCredentials | None:
    """Return decrypted LangSmith credentials, failing soft for optional tools."""
    return _langsmith_credentials(await _provider_for_tool_loading(login, LANGSMITH_KEY))


async def get_sandbox_langsmith_credentials(login: str) -> LangSmithCredentials | None:
    """Return sandbox credentials while surfacing lookup failures."""
    return _langsmith_credentials(await _get_provider(login, LANGSMITH_KEY))
