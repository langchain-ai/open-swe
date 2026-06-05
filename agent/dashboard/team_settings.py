"""Team-wide Open SWE Review (Bugbot) settings stored in LangGraph Store.

A single record keyed ``"default"`` keeps all instance-wide reviewer
configuration in one place. Per-repo style prompts live in
:mod:`agent.dashboard.review_styles`.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph_sdk import get_client
from pydantic import BaseModel, field_validator, model_validator

from .options import (
    SUPPORTED_MODEL_IDS,
    default_model_pair,
    model_supports_effort,
    provider_fallback_pair,
)

logger = logging.getLogger(__name__)

TEAM_SETTINGS_NAMESPACE: list[str] = ["team_settings"]
TEAM_SETTINGS_KEY = "default"

TriggerMode = Literal["every_push", "once_per_pr", "manual"]
AutofixMode = Literal["off", "low", "medium", "high"]

# Cap the org-wide guidelines so a runaway value can't dominate the reviewer
# prompt. Generous enough for a detailed policy, small enough to stay bounded.
ORG_GUIDELINES_MAX_CHARS = 10_000


class TeamSettingsUpdate(BaseModel):
    trigger_mode: TriggerMode = "every_push"
    review_draft_prs: bool = False
    pr_summaries: bool = True
    review_trace_links: bool = True
    autofix_mode: AutofixMode = "off"
    autofix_severity_threshold: AutofixMode = "medium"
    org_guidelines: str | None = None
    default_agent_model: str | None = None
    default_agent_reasoning_effort: str | None = None
    default_agent_subagent_model: str | None = None
    default_agent_subagent_reasoning_effort: str | None = None
    default_repo: str | None = None
    default_reviewer_model: str | None = None
    default_reviewer_reasoning_effort: str | None = None
    default_reviewer_subagent_model: str | None = None
    default_reviewer_subagent_reasoning_effort: str | None = None

    @field_validator("org_guidelines", mode="before")
    @classmethod
    def _normalize_org_guidelines(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("org_guidelines must be a string")
        text = v.strip()
        if not text:
            return None
        if len(text) > ORG_GUIDELINES_MAX_CHARS:
            raise ValueError(
                f"org_guidelines must be at most {ORG_GUIDELINES_MAX_CHARS} characters"
            )
        return text

    @model_validator(mode="after")
    def _validate_model_pairs(self) -> TeamSettingsUpdate:
        _validate_model_effort_pair(
            self.default_agent_model, self.default_agent_reasoning_effort, "agent"
        )
        _validate_model_effort_pair(
            self.default_agent_subagent_model,
            self.default_agent_subagent_reasoning_effort,
            "agent subagent",
        )
        _validate_model_effort_pair(
            self.default_reviewer_model, self.default_reviewer_reasoning_effort, "reviewer"
        )
        _validate_model_effort_pair(
            self.default_reviewer_subagent_model,
            self.default_reviewer_subagent_reasoning_effort,
            "reviewer subagent",
        )
        return self


def _validate_model_effort_pair(model: str | None, effort: str | None, role: str) -> None:
    if model is None and effort is None:
        return
    if model is None:
        raise ValueError(f"{role} reasoning effort set without a model")
    if model not in SUPPORTED_MODEL_IDS:
        raise ValueError(f"unsupported {role} model: {model}")
    if effort is None or not model_supports_effort(model, effort):
        raise ValueError(f"effort {effort!r} not supported by {role} model {model!r}")


def _client():
    return get_client()


def _env_default_repo() -> str | None:
    owner = os.environ.get("DEFAULT_REPO_OWNER", "").strip()
    name = os.environ.get("DEFAULT_REPO_NAME", "").strip()
    return f"{owner}/{name}" if owner and name else None


def _parse_repo(value: object) -> dict[str, str] | None:
    if not isinstance(value, str):
        return None
    owner, sep, name = value.strip().partition("/")
    if not sep or not owner.strip() or not name.strip():
        return None
    return {"owner": owner.strip(), "name": name.strip()}


def _default_settings() -> dict[str, Any]:
    fallback_model, fallback_effort = default_model_pair()
    return {
        "trigger_mode": "every_push",
        "review_draft_prs": False,
        "pr_summaries": True,
        "review_trace_links": True,
        "autofix_mode": "off",
        "autofix_severity_threshold": "medium",
        "org_guidelines": None,
        "default_agent_model": fallback_model,
        "default_agent_reasoning_effort": fallback_effort,
        "default_agent_subagent_model": fallback_model,
        "default_agent_subagent_reasoning_effort": fallback_effort,
        "default_repo": _env_default_repo(),
        "default_reviewer_model": fallback_model,
        "default_reviewer_reasoning_effort": fallback_effort,
        "default_reviewer_subagent_model": fallback_model,
        "default_reviewer_subagent_reasoning_effort": fallback_effort,
        "updated_at": None,
    }


async def get_team_settings() -> dict[str, Any]:
    defaults = _default_settings()
    try:
        item = await _client().store.get_item(TEAM_SETTINGS_NAMESPACE, TEAM_SETTINGS_KEY)
    except Exception as e:
        logger.debug("team settings lookup failed: %s", e)
        return defaults
    if item is None:
        return defaults
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict):
        return defaults
    # Skip None-valued model fields so legacy records (or PUTs that cleared the
    # selection) still surface the hardcoded default instead of a null.
    overlay = {k: v for k, v in value.items() if v is not None}
    merged = {**defaults, **overlay}
    # Drop obsolete trigger mode values so a legacy record doesn't surface a
    # value the new TriggerMode literal would reject on the next PUT.
    if merged.get("trigger_mode") not in {"every_push", "once_per_pr", "manual"}:
        merged["trigger_mode"] = defaults["trigger_mode"]
    return merged


async def upsert_team_settings(update: TeamSettingsUpdate) -> dict[str, Any]:
    value: dict[str, Any] = {
        "trigger_mode": update.trigger_mode,
        "review_draft_prs": update.review_draft_prs,
        "pr_summaries": update.pr_summaries,
        "review_trace_links": update.review_trace_links,
        "autofix_mode": update.autofix_mode,
        "autofix_severity_threshold": update.autofix_severity_threshold,
        "org_guidelines": update.org_guidelines,
        "default_agent_model": update.default_agent_model,
        "default_agent_reasoning_effort": update.default_agent_reasoning_effort,
        "default_agent_subagent_model": update.default_agent_subagent_model,
        "default_agent_subagent_reasoning_effort": update.default_agent_subagent_reasoning_effort,
        "default_repo": update.default_repo,
        "default_reviewer_model": update.default_reviewer_model,
        "default_reviewer_reasoning_effort": update.default_reviewer_reasoning_effort,
        "default_reviewer_subagent_model": update.default_reviewer_subagent_model,
        "default_reviewer_subagent_reasoning_effort": update.default_reviewer_subagent_reasoning_effort,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(TEAM_SETTINGS_NAMESPACE, TEAM_SETTINGS_KEY, value)
    return value


async def get_team_default_repo() -> dict[str, str] | None:
    settings = await get_team_settings()
    return _parse_repo(settings.get("default_repo"))


async def get_team_default_model(
    role: Literal["agent", "reviewer"],
) -> tuple[str, str]:
    """Return the team-wide default ``(model_id, reasoning_effort)`` for ``role``.

    Always returns a valid pair, resolved in order: the admin-configured pair if
    still supported; otherwise the newest supported model for the same provider
    (so a stale Anthropic/OpenAI selection stays on its provider rather than
    jumping cross-provider); otherwise the hardcoded global default from
    :func:`agent.dashboard.options.default_model_pair`.
    """
    settings = await get_team_settings()
    if role == "agent":
        model = settings.get("default_agent_model")
        effort = settings.get("default_agent_reasoning_effort")
    else:
        model = settings.get("default_reviewer_model")
        effort = settings.get("default_reviewer_reasoning_effort")
    return _resolve_default_pair(model, effort)


async def get_team_default_model_pair(
    role: Literal["agent", "reviewer"],
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return default ``(main, subagent)`` model pairs for ``role`` from one store read."""
    settings = await get_team_settings()
    if role == "agent":
        main = _resolve_default_pair(
            settings.get("default_agent_model"),
            settings.get("default_agent_reasoning_effort"),
        )
        subagent = _resolve_default_pair(
            settings.get("default_agent_subagent_model"),
            settings.get("default_agent_subagent_reasoning_effort"),
        )
    else:
        main = _resolve_default_pair(
            settings.get("default_reviewer_model"),
            settings.get("default_reviewer_reasoning_effort"),
        )
        subagent = _resolve_default_pair(
            settings.get("default_reviewer_subagent_model"),
            settings.get("default_reviewer_subagent_reasoning_effort"),
        )
    return main, subagent


async def get_team_review_trace_links_enabled() -> bool:
    """Return whether GitHub review bodies should include a LangSmith trace link."""
    settings = await get_team_settings()
    return bool(settings.get("review_trace_links", True))


async def get_org_review_guidelines() -> str | None:
    """Return the org-wide reviewer guidelines supplement, if configured."""
    settings = await get_team_settings()
    value = settings.get("org_guidelines")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def get_team_default_subagent_model(
    role: Literal["agent", "reviewer"],
) -> tuple[str, str]:
    """Return the team-wide default subagent ``(model_id, reasoning_effort)`` for ``role``."""
    settings = await get_team_settings()
    if role == "agent":
        model = settings.get("default_agent_subagent_model")
        effort = settings.get("default_agent_subagent_reasoning_effort")
    else:
        model = settings.get("default_reviewer_subagent_model")
        effort = settings.get("default_reviewer_subagent_reasoning_effort")
    return _resolve_default_pair(model, effort)


def _resolve_default_pair(model: object, effort: object) -> tuple[str, str]:
    """Supported pair if valid, else same-provider fallback, else global default."""
    if (
        isinstance(model, str)
        and isinstance(effort, str)
        and model in SUPPORTED_MODEL_IDS
        and model_supports_effort(model, effort)
    ):
        return model, effort
    provider_pair = provider_fallback_pair(model, effort)
    if provider_pair is not None:
        return provider_pair
    return default_model_pair()
