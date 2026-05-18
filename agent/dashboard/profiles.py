"""User profile schema and LangGraph Store CRUD.

Storage is split across namespaces to avoid read-modify-write races between
unrelated flows:

* ``["profiles"]`` — user-editable settings (model, effort, default_repo).
* ``["oauth_tokens"]`` — encrypted GitHub OAuth access token + work email.
  Canonical source for ``github_login → email`` resolution.
* ``["email_to_login"]`` — inverted index for ``email → github_login`` lookups.
  Kept in sync with ``oauth_tokens``.

Each upsert only touches its own namespace where possible. The OAuth-token
upsert is the one exception — it also writes the inverted-index entry — so
the two writes happen back-to-back but never clobber profile data.
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
EMAIL_TO_LOGIN_NAMESPACE: list[str] = ["email_to_login"]


def _normalize_email(email: str) -> str:
    return email.strip().lower()


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
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(PROFILES_NAMESPACE, login, value)
    return value


async def _update_email_index(login: str, new_email: str, *, prior_email: str | None) -> None:
    """Maintain the email→login inverted index alongside an oauth_tokens write.

    Drops a stale reverse-entry only when the user's email actually changed,
    so a typo'd write doesn't wipe the prior pointer.
    """
    if prior_email and prior_email != new_email:
        try:
            await _client().store.delete_item(EMAIL_TO_LOGIN_NAMESPACE, prior_email)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                logger.warning("Failed to drop stale email index for %s: %s", prior_email, e)
    if new_email:
        await _client().store.put_item(
            EMAIL_TO_LOGIN_NAMESPACE,
            new_email,
            {"login": login, "email": new_email, "updated_at": datetime.now(UTC).isoformat()},
        )


async def upsert_access_token(login: str, email: str, access_token: str) -> None:
    """Persist (or refresh) the user's encrypted GitHub OAuth token.

    Writes ``["oauth_tokens"]`` and the matching ``["email_to_login"]`` index
    entry. The user-editable profile in ``["profiles"]`` is untouched.
    """
    if not access_token:
        return
    normalized = _normalize_email(email) if email else ""
    existing = await _get_value(OAUTH_TOKENS_NAMESPACE, login) or {}
    prior_email = existing.get("email") if isinstance(existing.get("email"), str) else None
    value: dict[str, Any] = {
        "login": login,
        "email": normalized,
        "encrypted_gh_token": encrypt_token(access_token),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(OAUTH_TOKENS_NAMESPACE, login, value)
    await _update_email_index(login, normalized, prior_email=prior_email)


async def upsert_email_record(login: str, email: str) -> None:
    """Seed (or update) the email-only record for a github login.

    Used by the migration script and any flow that knows the user's email
    without yet having an OAuth token (e.g. seeded from the legacy hardcoded
    map). Idempotent: existing tokens are preserved if present.
    """
    if not login or not email:
        return
    normalized = _normalize_email(email)
    existing = await _get_value(OAUTH_TOKENS_NAMESPACE, login) or {}
    prior_email = existing.get("email") if isinstance(existing.get("email"), str) else None
    value: dict[str, Any] = {
        **existing,
        "login": login,
        "email": normalized,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(OAUTH_TOKENS_NAMESPACE, login, value)
    await _update_email_index(login, normalized, prior_email=prior_email)


async def get_access_token(login: str) -> str | None:
    record = await _get_value(OAUTH_TOKENS_NAMESPACE, login)
    if not record:
        return None
    encrypted = record.get("encrypted_gh_token")
    if not encrypted:
        return None
    return decrypt_token(encrypted) or None


async def get_email_for_github_login(login: str) -> str | None:
    if not login:
        return None
    record = await _get_value(OAUTH_TOKENS_NAMESPACE, login)
    if not record:
        return None
    email = record.get("email")
    return email if isinstance(email, str) and email else None


async def get_login_for_email(email: str) -> str | None:
    if not email:
        return None
    normalized = _normalize_email(email)
    record = await _get_value(EMAIL_TO_LOGIN_NAMESPACE, normalized)
    if not record:
        return None
    login = record.get("login")
    return login if isinstance(login, str) and login else None


async def list_profiles() -> list[dict[str, Any]]:
    result = await _client().store.search_items(PROFILES_NAMESPACE, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    return out
