"""User profile schema and storage.

Storage is split into two records to avoid the read-modify-write race
between profile-edit writes and OAuth-callback token refreshes:

* ``profile`` — user-editable settings (default_repo, branch, PR behavior).
* ``github_oauth_token`` — encrypted GitHub OAuth access token + email.

Each upsert only touches its own record, so the two flows can't clobber
each other's fields even when they interleave.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.oauth import (
    expires_at_from_github_response,
    is_unrecoverable_refresh_error,
    refresh_user_access_token,
    require_session,
)
from openswe.dashboard.oauth_refresh import refresh_guard
from openswe.encryption import decrypt_token, encrypt_token
from openswe.store import now_iso
from openswe.users import User, UserPreferences, UserPreferencesPatch
from openswe.users.records import UserRecords

logger = logging.getLogger(__name__)

PROFILES = UserRecords("profile")
GITHUB_OAUTH_TOKENS = UserRecords("github_oauth_token")


class ProfileUpdate(BaseModel):
    default_repo: str | None = None
    base_branch: str | None = None
    branch_prefix: str | None = None
    auto_fix_ci: bool = True
    recent_thread_context_enabled: bool = False
    concierge_mode: bool | None = None
    preserve_sandbox_memory: bool | None = None
    pr_review_links: bool | None = None
    pr_failure_reactions: bool | None = None
    prefer_tools_in_sandbox: bool | None = None
    experimental_task_coordination: bool | None = None
    draft_prs: bool | None = None
    review_draft_prs: bool | None = None
    experimental_assistant_ui: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    experimental_background_callbacks: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    experimental_act_as_approval: bool | None = None
    slack_onboarding_dismissed: bool = False


def normalize_profile_for_response(profile: dict[str, Any]) -> dict[str, Any]:
    value = dict(profile)
    for field in (
        "create_prs",
        "dm_session_enabled",
        "default_model",
        "reasoning_effort",
        "default_subagent_model",
        "subagent_reasoning_effort",
        "model_routing_enabled",
    ):
        value.pop(field, None)
    return value


async def get_profile(login: str) -> dict[str, Any] | None:
    return await PROFILES.get(login)


async def get_oauth_token_record(login: str) -> dict[str, Any] | None:
    """The raw encrypted-token record, for callers that need its expiry metadata."""
    return await GITHUB_OAUTH_TOKENS.get(login)


async def upsert_profile(login: str, email: str, update: ProfileUpdate) -> dict[str, Any]:
    """Write the user's editable settings.

    Only touches the profile record — the OAuth token record is untouched, so a concurrent re-login can't be clobbered by this write
    and vice versa.
    """
    existing = normalize_profile_for_response(await get_profile(login) or {})
    value: dict[str, Any] = {
        **existing,
        "login": login,
        "email": email or existing.get("email", ""),
        "default_repo": update.default_repo,
        "base_branch": update.base_branch,
        "branch_prefix": update.branch_prefix,
        "auto_fix_ci": update.auto_fix_ci,
        "recent_thread_context_enabled": (
            update.recent_thread_context_enabled
            if "recent_thread_context_enabled" in update.model_fields_set
            else existing.get("recent_thread_context_enabled", False)
        ),
        "draft_prs": (
            update.draft_prs if update.draft_prs is not None else existing.get("draft_prs", True)
        ),
        "review_draft_prs": update.review_draft_prs,
        "experimental_assistant_ui": (
            update.experimental_assistant_ui
            if update.experimental_assistant_ui is not None
            else existing.get("experimental_assistant_ui")
        ),
        "experimental_background_callbacks": (
            update.experimental_background_callbacks
            if update.experimental_background_callbacks is not None
            else existing.get("experimental_background_callbacks")
        ),
        "slack_onboarding_dismissed": (
            update.slack_onboarding_dismissed
            if "slack_onboarding_dismissed" in update.model_fields_set
            else existing.get("slack_onboarding_dismissed", False)
        ),
        "updated_at": now_iso(),
    }
    for stale_field in (
        "first_name",
        "last_name",
        "allow_artifacts",
        "slack_notifications",
        "preferred_pr_destination",
        "create_prs",
    ):
        value.pop(stale_field, None)
    await PROFILES.put(login, value)
    return value


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

    Only touches the token record — the user-editable profile is left
    intact even if a save is in flight in another request.
    """
    if not access_token:
        return
    existing = await GITHUB_OAUTH_TOKENS.get(login) or {}
    value: dict[str, Any] = {
        "login": login,
        "email": email or existing.get("email", ""),
        "encrypted_gh_token": encrypt_token(access_token),
        "updated_at": now_iso(),
    }
    if refresh_token:
        value["encrypted_gh_refresh_token"] = encrypt_token(refresh_token)
    elif existing.get("encrypted_gh_refresh_token"):
        value["encrypted_gh_refresh_token"] = existing["encrypted_gh_refresh_token"]
    if token_expires_at:
        value["token_expires_at"] = token_expires_at
    if refresh_token_expires_at:
        value["refresh_token_expires_at"] = refresh_token_expires_at
    await GITHUB_OAUTH_TOKENS.put(login, value)


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
    await GITHUB_OAUTH_TOKENS.delete(login)


async def mark_access_token_revoked(login: str, token: str) -> None:
    """Flag a stored token GitHub rejected so callers prompt a re-login."""
    record = await GITHUB_OAUTH_TOKENS.get(login)
    if record and _decrypt_access_token(record) == token:
        await GITHUB_OAUTH_TOKENS.put(login, {**record, "revoked": True})


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
        logger.warning("GitHub token refresh failed")
        return None, is_unrecoverable_refresh_error(exc)
    email_value = record.get("email")
    email = email_value if isinstance(email_value, str) else ""
    await upsert_access_token_from_github_response(login, email, data)
    access_token = data.get("access_token")
    return (access_token if isinstance(access_token, str) else None), False


async def get_valid_access_token(login: str, *, force_refresh: bool = False) -> str | None:
    """Return a GitHub access token, refreshing proactively when near expiry."""
    record = await GITHUB_OAUTH_TOKENS.get(login)
    if not record or record.get("revoked"):
        return None

    access_token = _decrypt_access_token(record)
    if not access_token:
        return None

    if not force_refresh and not _token_expired(record.get("token_expires_at")):
        return access_token

    if not _decrypt_refresh_token(record):
        return access_token

    async with refresh_guard("github", login):
        record = await GITHUB_OAUTH_TOKENS.get(login)
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
            latest = await GITHUB_OAUTH_TOKENS.get(login)
            if latest and latest.get("encrypted_gh_refresh_token") != record.get(
                "encrypted_gh_refresh_token"
            ):
                return _decrypt_access_token(latest)
            logger.info("Dropping dead GitHub authorization; re-login required")
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
    return bool(await GITHUB_OAUTH_TOKENS.get(login))


router = APIRouter(tags=["profiles"])
# Not openswe.dashboard.deps: that module imports repo_access, which imports this one.
_SESSION_DEP = Depends(require_session)


@router.get("/profile")
async def get_my_profile(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    profile, preferences = await asyncio.gather(
        get_profile(session["sub"]), User.preferences_for_login(session["sub"])
    )
    if not profile:
        return preferences.model_dump()
    return {**normalize_profile_for_response(profile), **preferences.model_dump()}


@router.post("/profile/slack-onboarding-dismissal")
async def dismiss_slack_onboarding(
    session: dict[str, str] = _SESSION_DEP,
) -> dict[str, object]:
    login = session["sub"]
    profile = await get_profile(login) or {}
    await PROFILES.put(
        login, {**profile, "slack_onboarding_dismissed": True, "updated_at": now_iso()}
    )
    return await get_my_profile(session)


@router.put("/profile")
@audit_endpoint
async def put_my_profile(
    update: ProfileUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    preferences = await User.update_preferences(
        login,
        UserPreferencesPatch(
            concierge_mode=update.concierge_mode,
            preserve_sandbox_memory=update.preserve_sandbox_memory,
            pr_review_links=update.pr_review_links,
            pr_failure_reactions=update.pr_failure_reactions,
            prefer_tools_in_sandbox=update.prefer_tools_in_sandbox,
            experimental_task_coordination=update.experimental_task_coordination,
            experimental_act_as_approval=update.experimental_act_as_approval,
            # Switching approval either way starts over from asking every time.
            act_as_always_allowed=(
                False if update.experimental_act_as_approval is not None else None
            ),
        ),
    )
    if preferences is None and (
        update.concierge_mode
        or update.preserve_sandbox_memory
        or update.pr_review_links
        or update.pr_failure_reactions
        or update.prefer_tools_in_sandbox
        or update.experimental_task_coordination
        or update.experimental_act_as_approval
    ):
        raise HTTPException(status_code=409, detail="No Open SWE user record for this login yet")
    profile = await upsert_profile(login, session.get("email") or "", update)
    return {
        **normalize_profile_for_response(profile),
        **(preferences or UserPreferences()).model_dump(),
    }
