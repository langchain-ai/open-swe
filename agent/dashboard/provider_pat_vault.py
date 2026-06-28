"""Per-user provider personal access token vault."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from langgraph_sdk import get_client

from ..encryption import decrypt_token, encrypt_token

PROVIDER_PAT_NAMESPACE: list[str] = ["provider_pat_vault"]
PROVIDER_PAT_AUDIT_NAMESPACE: list[str] = ["provider_pat_audit"]


@dataclass(frozen=True)
class ResolvedProviderPAT:
    login: str
    provider: str
    token: str
    token_last4: str
    updated_at: str | None = None


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_login(login: str) -> str:
    value = login.strip().lower()
    if not value:
        raise ValueError("login is required")
    return value


def _normalize_provider(provider: str) -> str:
    value = provider.strip().lower()
    if not value:
        raise ValueError("provider is required")
    return value


def _last4(value: str) -> str:
    return value[-4:] if len(value) >= 4 else value


def _value_from_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _redact(record: dict[str, Any] | None, *, provider: str) -> dict[str, Any]:
    if not record:
        return {"connected": False, "provider": provider}
    return {
        "connected": True,
        "login": record.get("login"),
        "provider": record.get("provider", provider),
        "token_last4": record.get("token_last4", ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


async def _get_record(login: str, provider: str) -> dict[str, Any] | None:
    login = _normalize_login(login)
    provider = _normalize_provider(provider)
    return _value_from_item(
        await _client().store.get_item([*PROVIDER_PAT_NAMESPACE, login], provider)
    )


async def list_provider_pat_status(login: str) -> list[dict[str, Any]]:
    login = _normalize_login(login)
    result = await _client().store.search_items([*PROVIDER_PAT_NAMESPACE, login], limit=100)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    statuses = [
        _redact(value, provider=str(value.get("provider", "")))
        for item in items or []
        if (value := _value_from_item(item)) is not None
    ]
    return sorted(statuses, key=lambda item: str(item.get("provider", "")))


async def get_provider_pat_status(login: str, *, provider: str) -> dict[str, Any]:
    provider = _normalize_provider(provider)
    return _redact(await _get_record(login, provider), provider=provider)


async def upsert_provider_pat(login: str, *, provider: str, token: str) -> dict[str, Any]:
    login = _normalize_login(login)
    provider = _normalize_provider(provider)
    token = token.strip()
    if not token:
        raise HTTPException(422, "token must be a non-empty string")
    existing = await _get_record(login, provider) or {}
    now = _now_iso()
    record = {
        "login": login,
        "provider": provider,
        "encrypted_token": encrypt_token(token),
        "token_last4": _last4(token),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    }
    await _client().store.put_item([*PROVIDER_PAT_NAMESPACE, login], provider, record)
    return _redact(record, provider=provider)


async def revoke_provider_pat(login: str, *, provider: str) -> dict[str, Any]:
    login = _normalize_login(login)
    provider = _normalize_provider(provider)
    await _client().store.delete_item([*PROVIDER_PAT_NAMESPACE, login], provider)
    return {"connected": False, "provider": provider}


async def _audit_token_resolution(
    *,
    login: str,
    project_id: str,
    provider: str,
    action: str,
    token_last4: str,
) -> dict[str, Any]:
    record = {
        "login": login,
        "project_id": project_id,
        "provider": provider,
        "action": action,
        "status": "resolved",
        "token_last4": token_last4,
        "created_at": _now_iso(),
    }
    key = f"{record['created_at']}:{secrets.token_hex(8)}"
    await _client().store.put_item(PROVIDER_PAT_AUDIT_NAMESPACE, key, record)
    return record


async def resolve_provider_pat(
    login: str,
    *,
    provider: str,
    project_id: str = "",
    action: str = "provider_access",
) -> ResolvedProviderPAT | None:
    login = _normalize_login(login)
    provider = _normalize_provider(provider)
    record = await _get_record(login, provider)
    if not record:
        return None
    token = decrypt_token(str(record.get("encrypted_token", "")))
    if not token:
        return None
    token_last4 = str(record.get("token_last4") or _last4(token))
    await _audit_token_resolution(
        login=login,
        project_id=project_id,
        provider=provider,
        action=action.strip() or "provider_access",
        token_last4=token_last4,
    )
    return ResolvedProviderPAT(
        login=login,
        provider=provider,
        token=token,
        token_last4=token_last4,
        updated_at=record.get("updated_at") if isinstance(record.get("updated_at"), str) else None,
    )


async def list_provider_pat_audit(login: str | None = None) -> list[dict[str, Any]]:
    filter_payload = {"login": _normalize_login(login)} if login else None
    result = await _client().store.search_items(
        PROVIDER_PAT_AUDIT_NAMESPACE,
        filter=filter_payload,
        limit=1000,
    )
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    records = [value for item in items or [] if (value := _value_from_item(item)) is not None]
    return sorted(records, key=lambda item: str(item.get("created_at", "")))
