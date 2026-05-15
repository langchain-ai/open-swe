"""User profile schema and LangGraph Store CRUD."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

from ..encryption import decrypt_token, encrypt_token
from .options import SUPPORTED_MODEL_IDS, model_supports_effort

logger = logging.getLogger(__name__)

PROFILES_NAMESPACE: list[str] = ["profiles"]


class ProfileUpdate(BaseModel):
    default_model: str
    reasoning_effort: str
    default_repo: str | None = None

    @field_validator("default_model")
    @classmethod
    def _model_supported(cls, v: str) -> str:
        if v not in SUPPORTED_MODEL_IDS:
            raise ValueError(f"unsupported model: {v}")
        return v

    def validate_pairing(self) -> None:
        if not model_supports_effort(self.default_model, self.reasoning_effort):
            raise ValueError(
                f"effort {self.reasoning_effort!r} not supported by {self.default_model!r}"
            )


def _client():
    return get_client()


async def _get_value(key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(PROFILES_NAMESPACE, key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value


async def get_profile(login: str) -> dict[str, Any] | None:
    return await _get_value(login)


async def upsert_profile(login: str, email: str, update: ProfileUpdate) -> dict[str, Any]:
    existing = await get_profile(login) or {}
    value: dict[str, Any] = {
        **existing,
        "login": login,
        "email": email or existing.get("email", ""),
        "default_model": update.default_model,
        "reasoning_effort": update.reasoning_effort,
        "default_repo": update.default_repo,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(PROFILES_NAMESPACE, login, value)
    return _redact(value)


async def upsert_access_token(login: str, email: str, access_token: str) -> None:
    """Persist (or refresh) the user's encrypted GitHub OAuth token on the profile."""
    existing = await get_profile(login) or {}
    value: dict[str, Any] = {
        **existing,
        "login": login,
        "email": email or existing.get("email", ""),
        "encrypted_gh_token": encrypt_token(access_token) if access_token else "",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(PROFILES_NAMESPACE, login, value)


async def get_access_token(login: str) -> str | None:
    profile = await get_profile(login)
    if not profile:
        return None
    encrypted = profile.get("encrypted_gh_token")
    if not encrypted:
        return None
    return decrypt_token(encrypted) or None


async def list_profiles() -> list[dict[str, Any]]:
    result = await _client().store.search_items(PROFILES_NAMESPACE, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if value:
            out.append(_redact(value))
    return out


def _redact(value: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(value)
    redacted.pop("encrypted_gh_token", None)
    return redacted
