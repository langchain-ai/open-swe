"""User profile schema and LangGraph Store CRUD.

Storage is split into two namespaces to avoid the read-modify-write race
between profile-edit writes and OAuth-callback token refreshes:

* ``["profiles"]`` — user-editable settings (model, effort, default_repo).
* ``["oauth_tokens"]`` — encrypted GitHub OAuth access token + email.

Each upsert only touches its own namespace, so the two flows can't clobber
each other's fields even when they interleave.
"""

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
OAUTH_TOKENS_NAMESPACE: list[str] = ["oauth_tokens"]


class ProfileUpdate(BaseModel):
    default_model: str
    reasoning_effort: str
    default_repo: str | None = None
    base_branch: str | None = None
    branch_prefix: str | None = None
    auto_fix_ci: bool = True
    create_prs: bool = True
    preferred_pr_destination: str | None = None

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


async def _get_value(namespace: list[str], key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(namespace, key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def get_profile(login: str) -> dict[str, Any] | None:
    return await _get_value(PROFILES_NAMESPACE, login)


async def upsert_profile(login: str, email: str, update: ProfileUpdate) -> dict[str, Any]:
    """Write the user's editable settings.

    Only touches ``["profiles"]`` — the OAuth token in ``["oauth_tokens"]``
    is untouched, so a concurrent re-login can't be clobbered by this write
    and vice versa.
    """
    existing = await get_profile(login) or {}
    value: dict[str, Any] = {
        **existing,
        "login": login,
        "email": email or existing.get("email", ""),
        "default_model": update.default_model,
        "reasoning_effort": update.reasoning_effort,
        "default_repo": update.default_repo,
        "base_branch": update.base_branch,
        "branch_prefix": update.branch_prefix,
        "auto_fix_ci": update.auto_fix_ci,
        "create_prs": update.create_prs,
        "preferred_pr_destination": update.preferred_pr_destination,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    for stale_field in (
        "first_name",
        "last_name",
        "allow_artifacts",
        "slack_notifications",
    ):
        value.pop(stale_field, None)
    await _client().store.put_item(PROFILES_NAMESPACE, login, value)
    return value


async def upsert_access_token(login: str, email: str, access_token: str) -> None:
    """Persist (or refresh) the user's encrypted GitHub OAuth token.

    Only touches ``["oauth_tokens"]`` — the user-editable profile is left
    intact even if a save is in flight in another request.
    """
    if not access_token:
        return
    value: dict[str, Any] = {
        "login": login,
        "email": email,
        "encrypted_gh_token": encrypt_token(access_token),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(OAUTH_TOKENS_NAMESPACE, login, value)


async def get_access_token(login: str) -> str | None:
    record = await _get_value(OAUTH_TOKENS_NAMESPACE, login)
    if not record:
        return None
    encrypted = record.get("encrypted_gh_token")
    if not encrypted:
        return None
    return decrypt_token(encrypted) or None


async def list_profiles() -> list[dict[str, Any]]:
    result = await _client().store.search_items(PROFILES_NAMESPACE, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    return out
