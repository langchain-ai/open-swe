"""Per-user third-party service credentials."""

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from ..encryption import decrypt_token, encrypt_token
from ..store import delete_value, get_value, now_iso, put_value
from .notion_oauth import is_reauth_required_error, refresh_notion_access_token
from .team_credentials import DEFAULT_LANGSMITH_ENDPOINT, LangSmithCredentials
from .token_vault import TokenFields, TokenVault, expires_at_from_response

logger = logging.getLogger(__name__)

USER_CREDENTIALS_NAMESPACE: list[str] = ["user_credentials"]
CURRENTS_KEY = "currents"
LANGSMITH_KEY = "langsmith"
NOTION_KEY = "notion"

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


@dataclass(frozen=True)
class NotionCredentials:
    access_token: str
    refresh_token: str | None
    token_endpoint: str
    client_id: str
    client_secret: str | None = None


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
        "updated_at": now_iso(),
    }
    token_type = data.get("token_type")
    if isinstance(token_type, str):
        record["token_type"] = token_type
    scope = data.get("scope")
    if isinstance(scope, str):
        record["scope"] = scope
    token_expires_at = expires_at_from_response(data)
    if token_expires_at:
        record["token_expires_at"] = token_expires_at
    elif existing.get("token_expires_at"):
        record["token_expires_at"] = existing["token_expires_at"]
    refresh_token_expires_at = expires_at_from_response(data, field="refresh_token_expires_in")
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


def _decrypt_notion_client_secret(record: dict[str, Any]) -> str | None:
    token = decrypt_token(record.get("encrypted_client_secret", ""))
    return token or None


async def _refresh_notion_tokens(
    *, login: str, refresh_token: str, record: dict[str, Any]
) -> dict[str, Any]:
    token_endpoint = record.get("token_endpoint")
    client_id = record.get("client_id")
    if not isinstance(token_endpoint, str) or not isinstance(client_id, str):
        raise ValueError(f"stored Notion credentials for {login} are incomplete")
    data = await refresh_notion_access_token(
        refresh_token=refresh_token,
        token_endpoint=token_endpoint,
        client_id=client_id,
        client_secret=_decrypt_notion_client_secret(record),
    )
    return _notion_record_from_response(data, existing=record)


_notion_vault = TokenVault(
    "Notion",
    locate=lambda login: (_namespace(login), NOTION_KEY),
    fields=TokenFields(access="encrypted_access_token", refresh="encrypted_refresh_token"),
    refresh=_refresh_notion_tokens,
    is_permanently_dead=is_reauth_required_error,
)


def _notion_credentials(record: dict[str, Any]) -> NotionCredentials | None:
    access_token = _notion_vault.access_token(record)
    if not access_token:
        return None
    return NotionCredentials(
        access_token=access_token,
        refresh_token=_notion_vault.refresh_token(record),
        token_endpoint=record.get("token_endpoint", ""),
        client_id=record.get("client_id", ""),
        client_secret=_decrypt_notion_client_secret(record),
    )


async def get_notion_credentials(
    login: str, *, force_refresh: bool = False
) -> NotionCredentials | None:
    """Return a valid Notion MCP credential set for a user.

    Fail-soft on purpose: this gates whether the Notion MCP tools get loaded
    into a run, so a store (or refresh) failure must cost the run those tools
    rather than the run itself.
    """
    try:
        record = await _notion_vault.get_valid(login, force_refresh=force_refresh)
    except Exception:
        logger.warning("Notion credential lookup failed for %s", login, exc_info=True)
        return None
    return _notion_credentials(record) if record else None


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


async def get_langsmith_credentials(login: str) -> LangSmithCredentials | None:
    """Return decrypted LangSmith credentials, or ``None`` when not connected."""
    langsmith = await _provider_for_tool_loading(login, LANGSMITH_KEY)
    if not isinstance(langsmith, dict):
        return None
    api_key = decrypt_token(langsmith.get("encrypted_api_key", ""))
    if not api_key:
        return None
    return LangSmithCredentials(api_key=api_key, endpoint=DEFAULT_LANGSMITH_ENDPOINT)
