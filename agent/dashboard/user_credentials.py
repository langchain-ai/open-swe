"""Per-user third-party service credentials (Currents.dev).

Credentials are encrypted at rest with :mod:`agent.encryption` and stored in a
dedicated LangGraph Store namespace, keyed by the user's GitHub login. The
sandbox never holds these keys — they feed server-side read-only tools.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

from ..encryption import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

USER_CREDENTIALS_NAMESPACE: list[str] = ["user_credentials"]
CURRENTS_KEY = "currents"

CURRENTS_API_BASE = "https://api.currents.dev/v1"


def _client():
    return get_client()


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


async def _get_provider(login: str, key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item([*USER_CREDENTIALS_NAMESPACE, login], key)
    except Exception as e:  # noqa: BLE001
        logger.debug("user credentials lookup failed for %s/%s: %s", login, key, e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _put_provider(login: str, key: str, value: dict[str, Any]) -> None:
    await _client().store.put_item([*USER_CREDENTIALS_NAMESPACE, login], key, value)


async def _delete_provider(login: str, key: str) -> None:
    try:
        await _client().store.delete_item([*USER_CREDENTIALS_NAMESPACE, login], key)
    except Exception as e:  # noqa: BLE001
        logger.debug("user credentials delete failed for %s/%s: %s", login, key, e)


class CurrentsCredentialsUpdate(BaseModel):
    """Connect Currents.dev with an organization API key."""

    api_key: str

    @field_validator("api_key")
    @classmethod
    def _require_non_empty(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("api_key must be a non-empty string")
        return v.strip()


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
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    return await get_currents_status(login)


async def disconnect_currents(login: str) -> dict[str, Any]:
    await _delete_provider(login, CURRENTS_KEY)
    return await get_currents_status(login)


async def get_currents_api_key(login: str) -> str | None:
    """Return the decrypted Currents API key, or ``None`` when not connected."""
    currents = await _get_provider(login, CURRENTS_KEY)
    if not isinstance(currents, dict):
        return None
    api_key = decrypt_token(currents.get("encrypted_api_key", ""))
    return api_key or None
