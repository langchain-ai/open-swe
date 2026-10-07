"""User profile schema and LangGraph Store CRUD.

Storage is split into two namespaces to avoid the read-modify-write race
between profile-edit writes and OAuth-callback token refreshes:

* ``["profiles"]`` — user-editable settings (model, effort, default_repo).
* ``["oauth_tokens"]`` — encrypted GitHub OAuth access token + email.

Each upsert only touches its own namespace, so the two flows can't clobber
each other's fields even when they interleave.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from openswe.dashboard.oauth import (
    expires_at_from_github_response,
    is_unrecoverable_refresh_error,
    refresh_user_access_token,
    require_session,
)
from openswe.dashboard.oauth_refresh import refresh_guard
from openswe.dashboard.options import (
    DEPRECATED_MODEL_IDS,
    NON_DEFAULT_MODEL_IDS,
    SUPPORTED_MODEL_IDS,
    model_supports_effort,
    provider_fallback_pair,
)
from openswe.encryption import decrypt_token, encrypt_token
from openswe.store import (
    delete_value,
    get_value,
    now_iso,
    put_value,
)
from openswe.users import User, UserPreferences, UserPreferencesPatch

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
    model_routing_enabled: bool | None = None
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
    experimental_mcp_ptc: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    experimental_act_as_approval: bool | None = None
    slack_onboarding_dismissed: bool = False

    @model_validator(mode="after")
    def _normalize_stale_model_pairs(self) -> ProfileUpdate:
        model, effort = _normalize_stale_model_pair(
            self.default_model,
            self.reasoning_effort,
        )
        self.default_model = model
        if effort is not None:
            self.reasoning_effort = effort
        if self.default_subagent_model is not None:
            self.default_subagent_model, self.subagent_reasoning_effort = (
                _normalize_stale_model_pair(
                    self.default_subagent_model,
                    self.subagent_reasoning_effort,
                )
            )
        return self

    def validate_pairing(self) -> None:
        if self.default_model in NON_DEFAULT_MODEL_IDS:
            raise ValueError(f"{self.default_model!r} cannot be a default model")
        if self.default_subagent_model in NON_DEFAULT_MODEL_IDS:
            raise ValueError(f"{self.default_subagent_model!r} cannot be a default model")
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


def _normalize_stale_model_pair(model: str, effort: str | None) -> tuple[str, str | None]:
    if model in SUPPORTED_MODEL_IDS or effort is None:
        return model, effort
    fallback = provider_fallback_pair(model, effort)
    if fallback is None:
        return model, effort
    return fallback


def normalize_profile_for_response(profile: dict[str, Any]) -> dict[str, Any]:
    value = dict(profile)
    value.pop("create_prs", None)
    value.pop("dm_session_enabled", None)
    for model_field, effort_field in (
        ("default_model", "reasoning_effort"),
        ("default_subagent_model", "subagent_reasoning_effort"),
    ):
        model = value.get(model_field)
        effort = value.get(effort_field)
        if model in DEPRECATED_MODEL_IDS or model in NON_DEFAULT_MODEL_IDS:
            value.pop(model_field, None)
            value.pop(effort_field, None)
        elif isinstance(model, str):
            value[model_field], value[effort_field] = _normalize_stale_model_pair(
                model, effort if isinstance(effort, str) else None
            )
    return value


async def get_profile(login: str) -> dict[str, Any] | None:
    return await get_value(PROFILES_NAMESPACE, login)


async def get_oauth_token_record(login: str) -> dict[str, Any] | None:
    """The raw encrypted-token record, for callers that need its expiry metadata."""
    return await get_value(OAUTH_TOKENS_NAMESPACE, login)


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
        "model_routing_enabled": (
            update.model_routing_enabled
            if "model_routing_enabled" in update.model_fields_set
            else existing.get("model_routing_enabled")
        ),
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
        "experimental_mcp_ptc": (
            update.experimental_mcp_ptc
            if update.experimental_mcp_ptc is not None
            else existing.get("experimental_mcp_ptc", False)
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
    await put_value(PROFILES_NAMESPACE, login, value)
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

    Only touches ``["oauth_tokens"]`` — the user-editable profile is left
    intact even if a save is in flight in another request.
    """
    if not access_token:
        return
    existing = await get_value(OAUTH_TOKENS_NAMESPACE, login) or {}
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
    await put_value(OAUTH_TOKENS_NAMESPACE, login, value)


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
    await delete_value(OAUTH_TOKENS_NAMESPACE, login)


async def mark_access_token_revoked(login: str, token: str) -> None:
    """Flag a stored token GitHub rejected so callers prompt a re-login."""
    record = await get_value(OAUTH_TOKENS_NAMESPACE, login)
    if record and _decrypt_access_token(record) == token:
        await put_value(OAUTH_TOKENS_NAMESPACE, login, {**record, "revoked": True})


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
    record = await get_value(OAUTH_TOKENS_NAMESPACE, login)
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
        record = await get_value(OAUTH_TOKENS_NAMESPACE, login)
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
            latest = await get_value(OAUTH_TOKENS_NAMESPACE, login)
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
    return bool(await get_value(OAUTH_TOKENS_NAMESPACE, login))


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
    await put_value(
        PROFILES_NAMESPACE,
        login,
        {**profile, "slack_onboarding_dismissed": True, "updated_at": now_iso()},
    )
    return await get_my_profile(session)


@router.put("/profile")
async def put_my_profile(
    update: ProfileUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    update.validate_pairing()
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
