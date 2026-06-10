"""Team-wide observability provider credentials (Datadog, LangSmith).

Credentials are encrypted at rest with :mod:`agent.encryption` and stored in a
dedicated LangGraph Store namespace, separate from the plaintext team settings
record so a settings read never surfaces a secret. They feed the server-side
Datadog MCP tools and LangSmith read tools — the sandbox never sees these keys.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

from ..encryption import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

TEAM_CREDENTIALS_NAMESPACE: list[str] = ["team_credentials"]
DATADOG_KEY = "datadog"
LANGSMITH_KEY = "langsmith"

# Datadog sites that expose a hosted MCP server. The MCP host is derived by
# swapping the leading ``app.`` (or bare site) for ``mcp.``.
DEFAULT_DD_SITE = "datadoghq.com"
SUPPORTED_DD_SITES: frozenset[str] = frozenset(
    {
        "datadoghq.com",
        "us3.datadoghq.com",
        "us5.datadoghq.com",
        "datadoghq.eu",
        "ap1.datadoghq.com",
        "ap2.datadoghq.com",
    }
)


def _client():
    return get_client()


class DatadogCredentialsUpdate(BaseModel):
    """Connect Datadog with a scoped API + application key pair."""

    site: str = DEFAULT_DD_SITE
    api_key: str
    app_key: str

    @field_validator("site", mode="before")
    @classmethod
    def _normalize_site(cls, v: object) -> str:
        if v is None:
            return DEFAULT_DD_SITE
        if not isinstance(v, str):
            raise ValueError("site must be a string")
        site = v.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        site = site.removeprefix("app.")
        if not site:
            return DEFAULT_DD_SITE
        if site not in SUPPORTED_DD_SITES:
            raise ValueError(f"unsupported Datadog site: {site}")
        return site

    @field_validator("api_key", "app_key")
    @classmethod
    def _require_non_empty(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("key must be a non-empty string")
        return v.strip()


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
class DatadogCredentials:
    site: str
    api_key: str
    app_key: str

    @property
    def mcp_host(self) -> str:
        return f"mcp.{self.site}"

    def mcp_url(self, toolsets: str) -> str:
        url = f"https://{self.mcp_host}/api/unstable/mcp-server/mcp"
        return f"{url}?toolsets={toolsets}" if toolsets else url


@dataclass(frozen=True)
class LangSmithCredentials:
    api_key: str
    endpoint: str


DEFAULT_LANGSMITH_ENDPOINT = "https://api.smith.langchain.com"


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


async def _get_provider(key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(TEAM_CREDENTIALS_NAMESPACE, key)
    except Exception as e:  # noqa: BLE001
        logger.debug("team credentials lookup failed for %s: %s", key, e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _put_provider(key: str, value: dict[str, Any]) -> None:
    await _client().store.put_item(TEAM_CREDENTIALS_NAMESPACE, key, value)


async def _delete_provider(key: str) -> None:
    try:
        await _client().store.delete_item(TEAM_CREDENTIALS_NAMESPACE, key)
    except Exception as e:  # noqa: BLE001
        logger.debug("team credentials delete failed for %s: %s", key, e)


async def get_team_credentials_status() -> dict[str, Any]:
    """Return a redacted, dashboard-safe view of connected providers."""
    datadog = await _get_provider(DATADOG_KEY)
    langsmith = await _get_provider(LANGSMITH_KEY)
    return {
        "datadog": {
            "connected": True,
            "site": datadog.get("site", DEFAULT_DD_SITE),
            "api_key_last4": datadog.get("api_key_last4", ""),
            "updated_at": datadog.get("updated_at"),
        }
        if datadog
        else {"connected": False},
        "langsmith": {
            "connected": True,
            "endpoint": langsmith.get("endpoint", DEFAULT_LANGSMITH_ENDPOINT),
            "api_key_last4": langsmith.get("api_key_last4", ""),
            "updated_at": langsmith.get("updated_at"),
        }
        if langsmith
        else {"connected": False},
    }


async def connect_datadog(update: DatadogCredentialsUpdate) -> dict[str, Any]:
    await _put_provider(
        DATADOG_KEY,
        {
            "site": update.site,
            "encrypted_api_key": encrypt_token(update.api_key),
            "encrypted_app_key": encrypt_token(update.app_key),
            "api_key_last4": _last4(update.api_key),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    return await get_team_credentials_status()


async def disconnect_datadog() -> dict[str, Any]:
    await _delete_provider(DATADOG_KEY)
    return await get_team_credentials_status()


async def connect_langsmith(update: LangSmithCredentialsUpdate) -> dict[str, Any]:
    await _put_provider(
        LANGSMITH_KEY,
        {
            "endpoint": update.endpoint or DEFAULT_LANGSMITH_ENDPOINT,
            "encrypted_api_key": encrypt_token(update.api_key),
            "api_key_last4": _last4(update.api_key),
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    return await get_team_credentials_status()


async def disconnect_langsmith() -> dict[str, Any]:
    await _delete_provider(LANGSMITH_KEY)
    return await get_team_credentials_status()


async def get_datadog_credentials() -> DatadogCredentials | None:
    """Return decrypted Datadog credentials, or ``None`` when not connected."""
    datadog = await _get_provider(DATADOG_KEY)
    if not isinstance(datadog, dict):
        return None
    api_key = decrypt_token(datadog.get("encrypted_api_key", ""))
    app_key = decrypt_token(datadog.get("encrypted_app_key", ""))
    if not api_key or not app_key:
        return None
    return DatadogCredentials(
        site=datadog.get("site", DEFAULT_DD_SITE),
        api_key=api_key,
        app_key=app_key,
    )


async def get_langsmith_credentials() -> LangSmithCredentials | None:
    """Return decrypted LangSmith credentials, or ``None`` when not connected."""
    langsmith = await _get_provider(LANGSMITH_KEY)
    if not isinstance(langsmith, dict):
        return None
    api_key = decrypt_token(langsmith.get("encrypted_api_key", ""))
    if not api_key:
        return None
    return LangSmithCredentials(
        api_key=api_key,
        endpoint=langsmith.get("endpoint", DEFAULT_LANGSMITH_ENDPOINT),
    )
