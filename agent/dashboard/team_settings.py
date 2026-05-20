"""Team-wide Open SWE Review (Bugbot) settings stored in LangGraph Store.

A single record keyed ``"default"`` keeps all instance-wide reviewer
configuration in one place. Per-repo style prompts live in
:mod:`agent.dashboard.review_styles`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph_sdk import get_client
from pydantic import BaseModel, model_validator

from .options import SUPPORTED_MODEL_IDS, model_supports_effort

logger = logging.getLogger(__name__)

TEAM_SETTINGS_NAMESPACE: list[str] = ["team_settings"]
TEAM_SETTINGS_KEY = "default"

TriggerMode = Literal["every_push", "ready_for_review", "manual"]
AutofixMode = Literal["off", "low", "medium", "high"]


class TeamSettingsUpdate(BaseModel):
    trigger_mode: TriggerMode = "every_push"
    review_draft_prs: bool = False
    pr_summaries: bool = True
    autofix_mode: AutofixMode = "off"
    autofix_severity_threshold: AutofixMode = "medium"
    default_agent_model: str | None = None
    default_agent_reasoning_effort: str | None = None
    default_reviewer_model: str | None = None
    default_reviewer_reasoning_effort: str | None = None

    @model_validator(mode="after")
    def _validate_model_pairs(self) -> TeamSettingsUpdate:
        _validate_model_effort_pair(
            self.default_agent_model, self.default_agent_reasoning_effort, "agent"
        )
        _validate_model_effort_pair(
            self.default_reviewer_model, self.default_reviewer_reasoning_effort, "reviewer"
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


def _default_settings() -> dict[str, Any]:
    return {
        "trigger_mode": "every_push",
        "review_draft_prs": False,
        "pr_summaries": True,
        "autofix_mode": "off",
        "autofix_severity_threshold": "medium",
        "default_agent_model": None,
        "default_agent_reasoning_effort": None,
        "default_reviewer_model": None,
        "default_reviewer_reasoning_effort": None,
        "updated_at": None,
    }


async def get_team_settings() -> dict[str, Any]:
    try:
        item = await _client().store.get_item(TEAM_SETTINGS_NAMESPACE, TEAM_SETTINGS_KEY)
    except Exception as e:
        logger.debug("team settings lookup failed: %s", e)
        return _default_settings()
    if item is None:
        return _default_settings()
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict):
        return _default_settings()
    return {**_default_settings(), **value}


async def upsert_team_settings(update: TeamSettingsUpdate) -> dict[str, Any]:
    value: dict[str, Any] = {
        "trigger_mode": update.trigger_mode,
        "review_draft_prs": update.review_draft_prs,
        "pr_summaries": update.pr_summaries,
        "autofix_mode": update.autofix_mode,
        "autofix_severity_threshold": update.autofix_severity_threshold,
        "default_agent_model": update.default_agent_model,
        "default_agent_reasoning_effort": update.default_agent_reasoning_effort,
        "default_reviewer_model": update.default_reviewer_model,
        "default_reviewer_reasoning_effort": update.default_reviewer_reasoning_effort,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(TEAM_SETTINGS_NAMESPACE, TEAM_SETTINGS_KEY, value)
    return value


async def get_team_model_override(
    role: Literal["agent", "reviewer"],
) -> tuple[str | None, str | None]:
    """Return ``(model_id, reasoning_effort)`` for the team-wide default, or ``(None, None)``.

    Returns the override only when both fields are valid and form a supported pair.
    """
    settings = await get_team_settings()
    if role == "agent":
        model = settings.get("default_agent_model")
        effort = settings.get("default_agent_reasoning_effort")
    else:
        model = settings.get("default_reviewer_model")
        effort = settings.get("default_reviewer_reasoning_effort")
    if not isinstance(model, str) or model not in SUPPORTED_MODEL_IDS:
        return None, None
    if not isinstance(effort, str) or not model_supports_effort(model, effort):
        return None, None
    return model, effort
