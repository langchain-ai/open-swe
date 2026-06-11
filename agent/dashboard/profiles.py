"""User profile schema and LangGraph Store CRUD.

Storage is split into two namespaces to avoid the read-modify-write race
between profile-edit writes and OAuth-callback token refreshes:

* ``["profiles"]`` — user-editable settings (model, effort, default_repo).
* ``["oauth_tokens"]`` — encrypted GitHub OAuth access token + email.

Each upsert only touches its own namespace, so the two flows can't clobber
each other's fields even when they interleave.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator

from ..encryption import decrypt_token, encrypt_token
from .oauth import (
    expires_at_from_github_response,
    is_unrecoverable_refresh_error,
    refresh_user_access_token,
)
from .options import SUPPORTED_MODEL_IDS, model_supports_effort

logger = logging.getLogger(__name__)

PROFILES_NAMESPACE: list[str] = ["profiles"]
OAUTH_TOKENS_NAMESPACE: list[str] = ["oauth_tokens"]


class ProfileUpdate(BaseModel):
    default_model: str
    reasoning_effort: str
    default_subagent_model: str | None = None
    subagent_reasoning_effort: str | None = None
    default_repo: str | None = None
    base_branch: str | None = None
    branch_prefix: str | None = None
    auto_fix_ci: bool = True
    create_prs: bool = False
    review_draft_prs: bool | None = None

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
        if self.default_subagent_model is None and self.subagent_reasoning_effort is None:
            return
        if self.default_subagent_model is None:
            raise ValueError("subagent reasoning effort set without a model")
        if self.default_subagent_model not in SUPPORTED_MODEL_IDS:
            raise ValueError(f"unsupported subagent model: {self.default_subagent_model}")
        if self.subagent_reasoning_effort is None or not model_supports_effort(
            self.default_subagent_model,
            self.subagent_reasoning_effort,
        ):
            raise ValueError(
                f"effort {self.subagent_reasoning_effort!r} not supported by "
                f"{self.default_subagent_model!r}"
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
        "default_subagent_model": update.default_subagent_model,
        "subagent_reasoning_effort": update.subagent_reasoning_effort,
        "default_repo": update.default_repo,
        "base_branch": update.base_branch,
        "branch_prefix": update.branch_prefix,
        "auto_fix_ci": update.auto_fix_ci,
        "create_prs": update.create_prs,
        "review_draft_prs": update.review_draft_prs,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    for stale_field in (
        "first_name",
        "last_name",
        "allow_artifacts",
        "slack_notifications",
        "preferred_pr_destination",
    ):
        value.pop(stale_field, None)
    await _client().store.put_item(PROFILES_NAMESPACE, login, value)
    return value


_refresh_locks: dict[str, asyncio.Lock] = {}


def _refresh_lock(login: str) -> asyncio.Lock:
    lock = _refresh_locks.get(login)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[login] = lock
    return lock


def _token_expired(expires_at: str | None, *, skew_seconds: int = 300) -> bool:
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
    except ValueError:
        return False
    return datetime.now(UTC) + timedelta(seconds=skew_seconds) >= exp


async def upsert_access_token(
    login: str,
    email: str,
    access_token: str,
    *,
    refresh_token: str | None = None,
    token_expires_at: str | None = None,
    refresh_token_expires_at: str | None = None,
) -> None:
    """Persist (or refresh) the user's encrypted GitHub OAuth tokens.

    Only touches ``["oauth_tokens"]`` — the user-editable profile is left
    intact even if a save is in flight in another request.
    """
    if not access_token:
        return
    existing = await _get_value(OAUTH_TOKENS_NAMESPACE, login) or {}
    value: dict[str, Any] = {
        "login": login,
        "email": email or existing.get("email", ""),
        "encrypted_gh_token": encrypt_token(access_token),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if refresh_token:
        value["encrypted_gh_refresh_token"] = encrypt_token(refresh_token)
    elif existing.get("encrypted_gh_refresh_token"):
        value["encrypted_gh_refresh_token"] = existing["encrypted_gh_refresh_token"]
    if token_expires_at:
        value["token_expires_at"] = token_expires_at
    if refresh_token_expires_at:
        value["refresh_token_expires_at"] = refresh_token_expires_at
    await _client().store.put_item(OAUTH_TOKENS_NAMESPACE, login, value)


async def upsert_access_token_from_github_response(
    login: str, email: str, data: dict[str, Any]
) -> None:
    """Store tokens from a GitHub OAuth code exchange or refresh response."""
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return
    refresh_token = data.get("refresh_token")
    await upsert_access_token(
        login,
        email,
        access_token,
        refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        token_expires_at=expires_at_from_github_response(data, field="expires_in"),
        refresh_token_expires_at=expires_at_from_github_response(
            data, field="refresh_token_expires_in"
        ),
    )


async def delete_access_token(login: str) -> None:
    """Drop the user's stored OAuth tokens.

    Used when a refresh token is permanently dead so we stop handing out a
    known-stale access token and callers prompt a clean re-login instead.
    """
    try:
        await _client().store.delete_item(OAUTH_TOKENS_NAMESPACE, login)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise


def _decrypt_access_token(record: dict[str, Any]) -> str | None:
    encrypted = record.get("encrypted_gh_token")
    if not encrypted:
        return None
    return decrypt_token(encrypted) or None


def _decrypt_refresh_token(record: dict[str, Any]) -> str | None:
    encrypted = record.get("encrypted_gh_refresh_token")
    if not encrypted:
        return None
    return decrypt_token(encrypted) or None


async def _refresh_stored_token(login: str, record: dict[str, Any]) -> tuple[str | None, bool]:
    """Refresh the stored token, returning ``(access_token, refresh_token_dead)``.

    ``refresh_token_dead`` is True when GitHub says the refresh token can never
    mint a new token again, so the caller should drop the stored authorization
    rather than keep serving a stale access token.
    """
    refresh_token = _decrypt_refresh_token(record)
    if not refresh_token:
        return None, False
    try:
        data = await refresh_user_access_token(refresh_token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GitHub token refresh failed for %s", login, exc_info=True)
        return None, is_unrecoverable_refresh_error(exc)
    email = record.get("email") if isinstance(record.get("email"), str) else ""
    await upsert_access_token_from_github_response(login, email, data)
    access_token = data.get("access_token")
    return (access_token if isinstance(access_token, str) else None), False


async def get_valid_access_token(login: str, *, force_refresh: bool = False) -> str | None:
    """Return a GitHub access token, refreshing proactively when near expiry."""
    record = await _get_value(OAUTH_TOKENS_NAMESPACE, login)
    if not record:
        return None

    access_token = _decrypt_access_token(record)
    if not access_token:
        return None

    if not force_refresh and not _token_expired(record.get("token_expires_at")):
        return access_token

    if not _decrypt_refresh_token(record):
        return access_token

    async with _refresh_lock(login):
        record = await _get_value(OAUTH_TOKENS_NAMESPACE, login)
        if not record:
            return None
        access_token = _decrypt_access_token(record)
        if not access_token:
            return None
        if not force_refresh and not _token_expired(record.get("token_expires_at")):
            return access_token
        refreshed, refresh_token_dead = await _refresh_stored_token(login, record)
        if refreshed:
            return refreshed
        if refresh_token_dead:
            # The refresh token is permanently invalid (revoked / expired), so
            # the cached access token is dead too. Drop it so callers prompt a
            # clean re-login instead of repeatedly handing out a stale token.
            # The OAuth callback can write a fresh authorization while the
            # refresh request is in flight (it doesn't take this lock), so only
            # delete if the stored record is still the one that failed.
            latest = await _get_value(OAUTH_TOKENS_NAMESPACE, login)
            if latest and latest.get("encrypted_gh_refresh_token") != record.get(
                "encrypted_gh_refresh_token"
            ):
                return _decrypt_access_token(latest)
            logger.info("Dropping dead GitHub authorization for %s; re-login required", login)
            await delete_access_token(login)
            return None
        return access_token


async def get_access_token(login: str) -> str | None:
    return await get_valid_access_token(login)


async def has_access_token_record(login: str) -> bool:
    """Whether an OAuth token record exists for ``login``.

    Distinguishes "user has never completed a GitHub login" (no record) from
    "the stored authorization is present but no longer usable" (record exists
    but won't decrypt / was revoked), so callers can prompt accurately.
    """
    return bool(await _get_value(OAUTH_TOKENS_NAMESPACE, login))


async def list_profiles() -> list[dict[str, Any]]:
    result = await _client().store.search_items(PROFILES_NAMESPACE, limit=1000)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    out: list[dict[str, Any]] = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            out.append(value)
    return out
