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
from pydantic import BaseModel

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


def _client():
    return get_client()


def _default_settings() -> dict[str, Any]:
    return {
        "trigger_mode": "every_push",
        "review_draft_prs": False,
        "pr_summaries": True,
        "autofix_mode": "off",
        "autofix_severity_threshold": "medium",
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
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(TEAM_SETTINGS_NAMESPACE, TEAM_SETTINGS_KEY, value)
    return value
