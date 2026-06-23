"""Per-user third-party service credentials."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

from ..encryption import decrypt_token, encrypt_token
from .notion_oauth import is_reauth_required_error, refresh_notion_access_token

logger = logging.getLogger(__name__)

USER_CREDENTIALS_NAMESPACE: list[str] = ["user_credentials"]
CURRENTS_KEY = "currents"
NOTION_KEY = "notion"

CURRENTS_API_BASE = "https://api.currents.dev/v1"
_NOTION_TOKEN_EXPIRY_SKEW_SECONDS = 300


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


@dataclass(frozen=True)
class NotionCredentials:
    access_token: str
    refresh_token: str | None
    token_endpoint: str
    client_id: str
    client_secret: str | None = None


def _expires_at_from_response(data: dict[str, Any], *, field: str = "expires_in") -> str | None:
    raw = data.get(field)
    if not isinstance(raw, int | float) or raw <= 0:
        return None
    return (datetime.now(UTC) + timedelta(seconds=int(raw))).isoformat()


def _token_expired(
    expires_at: str | None, *, skew_seconds: int = _NOTION_TOKEN_EXPIRY_SKEW_SECONDS
) -> bool:
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
    except ValueError:
        return False
    return datetime.now(UTC) + timedelta(seconds=skew_seconds) >= exp


async def get_notion_status(login: str) -> dict[str, Any]:
    """Return a redacted view of the user's Notion MCP connection."""
    notion = await _get_provider(login, NOTION_KEY)
    return {
        "notion": {
            "connected": True,
            "token_expires_at": notion.get("token_expires_at"),
            "updated_at": notion.get("updated_at"),
        }
        if notion
        else {"connected": False},
    }


def _notion_record_from_response(
    data: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    token_endpoint: str | None = None,
) -> dict[str, Any]:
    existing = existing or {}
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Notion OAuth response missing access_token")
    refresh_token = data.get("refresh_token")
    record: dict[str, Any] = {
        "encrypted_access_token": encrypt_token(access_token),
        "client_id": client_id or existing.get("client_id", ""),
        "token_endpoint": token_endpoint or existing.get("token_endpoint", ""),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    token_type = data.get("token_type")
    if isinstance(token_type, str):
        record["token_type"] = token_type
    scope = data.get("scope")
    if isinstance(scope, str):
        record["scope"] = scope
    token_expires_at = _expires_at_from_response(data)
    if token_expires_at:
        record["token_expires_at"] = token_expires_at
    elif existing.get("token_expires_at"):
        record["token_expires_at"] = existing["token_expires_at"]
    refresh_token_expires_at = _expires_at_from_response(data, field="refresh_token_expires_in")
    if refresh_token_expires_at:
        record["refresh_token_expires_at"] = refresh_token_expires_at
    elif existing.get("refresh_token_expires_at"):
        record["refresh_token_expires_at"] = existing["refresh_token_expires_at"]
    if isinstance(refresh_token, str) and refresh_token:
        record["encrypted_refresh_token"] = encrypt_token(refresh_token)
    elif existing.get("encrypted_refresh_token"):
        record["encrypted_refresh_token"] = existing["encrypted_refresh_token"]
    if client_secret:
        record["encrypted_client_secret"] = encrypt_token(client_secret)
    elif existing.get("encrypted_client_secret"):
        record["encrypted_client_secret"] = existing["encrypted_client_secret"]
    return record


async def connect_notion(login: str, data: dict[str, Any], flow: dict[str, Any]) -> dict[str, Any]:
    client_id = flow.get("client_id")
    token_endpoint = flow.get("token_endpoint")
    if not isinstance(client_id, str) or not isinstance(token_endpoint, str):
        raise ValueError("stored Notion OAuth flow is incomplete")
    client_secret = (
        flow.get("client_secret") if isinstance(flow.get("client_secret"), str) else None
    )
    await _put_provider(
        login,
        NOTION_KEY,
        _notion_record_from_response(
            data,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint=token_endpoint,
        ),
    )
    return await get_notion_status(login)


async def disconnect_notion(login: str) -> dict[str, Any]:
    await _delete_provider(login, NOTION_KEY)
    return await get_notion_status(login)


def _decrypt_notion_access_token(record: dict[str, Any]) -> str | None:
    token = decrypt_token(record.get("encrypted_access_token", ""))
    return token or None


def _decrypt_notion_refresh_token(record: dict[str, Any]) -> str | None:
    token = decrypt_token(record.get("encrypted_refresh_token", ""))
    return token or None


def _decrypt_notion_client_secret(record: dict[str, Any]) -> str | None:
    token = decrypt_token(record.get("encrypted_client_secret", ""))
    return token or None


_notion_refresh_locks: dict[str, asyncio.Lock] = {}


def _notion_refresh_lock(login: str) -> asyncio.Lock:
    lock = _notion_refresh_locks.get(login)
    if lock is None:
        lock = asyncio.Lock()
        _notion_refresh_locks[login] = lock
    return lock


async def _refresh_stored_notion_token(
    login: str,
    record: dict[str, Any],
) -> tuple[str | None, bool]:
    refresh_token = _decrypt_notion_refresh_token(record)
    token_endpoint = record.get("token_endpoint")
    client_id = record.get("client_id")
    if not refresh_token or not isinstance(token_endpoint, str) or not isinstance(client_id, str):
        return None, False
    try:
        data = await refresh_notion_access_token(
            refresh_token=refresh_token,
            token_endpoint=token_endpoint,
            client_id=client_id,
            client_secret=_decrypt_notion_client_secret(record),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notion token refresh failed for %s", login, exc_info=True)
        return None, is_reauth_required_error(exc)
    await _put_provider(login, NOTION_KEY, _notion_record_from_response(data, existing=record))
    access_token = data.get("access_token")
    return (access_token if isinstance(access_token, str) else None), False


async def get_notion_credentials(
    login: str, *, force_refresh: bool = False
) -> NotionCredentials | None:
    """Return a valid Notion MCP credential set for a user."""
    record = await _get_provider(login, NOTION_KEY)
    if not record:
        return None
    access_token = _decrypt_notion_access_token(record)
    if not access_token:
        return None
    if not force_refresh and not _token_expired(record.get("token_expires_at")):
        return NotionCredentials(
            access_token=access_token,
            refresh_token=_decrypt_notion_refresh_token(record),
            token_endpoint=record.get("token_endpoint", ""),
            client_id=record.get("client_id", ""),
            client_secret=_decrypt_notion_client_secret(record),
        )
    if not _decrypt_notion_refresh_token(record):
        return None
    async with _notion_refresh_lock(login):
        record = await _get_provider(login, NOTION_KEY)
        if not record:
            return None
        access_token = _decrypt_notion_access_token(record)
        if not access_token:
            return None
        if not force_refresh and not _token_expired(record.get("token_expires_at")):
            return NotionCredentials(
                access_token=access_token,
                refresh_token=_decrypt_notion_refresh_token(record),
                token_endpoint=record.get("token_endpoint", ""),
                client_id=record.get("client_id", ""),
                client_secret=_decrypt_notion_client_secret(record),
            )
        refreshed, refresh_token_dead = await _refresh_stored_notion_token(login, record)
        if refreshed:
            refreshed_record = await _get_provider(login, NOTION_KEY) or record
            return NotionCredentials(
                access_token=refreshed,
                refresh_token=_decrypt_notion_refresh_token(refreshed_record),
                token_endpoint=refreshed_record.get("token_endpoint", ""),
                client_id=refreshed_record.get("client_id", ""),
                client_secret=_decrypt_notion_client_secret(refreshed_record),
            )
        if refresh_token_dead:
            latest = await _get_provider(login, NOTION_KEY)
            if latest and latest.get("encrypted_refresh_token") != record.get(
                "encrypted_refresh_token"
            ):
                latest_access_token = _decrypt_notion_access_token(latest)
                if latest_access_token:
                    return NotionCredentials(
                        access_token=latest_access_token,
                        refresh_token=_decrypt_notion_refresh_token(latest),
                        token_endpoint=latest.get("token_endpoint", ""),
                        client_id=latest.get("client_id", ""),
                        client_secret=_decrypt_notion_client_secret(latest),
                    )
            logger.info("Dropping dead Notion authorization for %s; reconnect required", login)
            await disconnect_notion(login)
            return None
        return NotionCredentials(
            access_token=access_token,
            refresh_token=_decrypt_notion_refresh_token(record),
            token_endpoint=record.get("token_endpoint", ""),
            client_id=record.get("client_id", ""),
            client_secret=_decrypt_notion_client_secret(record),
        )


async def get_notion_access_token(login: str) -> str | None:
    credentials = await get_notion_credentials(login)
    return credentials.access_token if credentials else None


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
