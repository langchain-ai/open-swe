"""Tool: ``enter_plan_mode``. Switch the run into read-only planning."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.types import Command

from ..dashboard.plan_store import PLAN_STATUS_PLANNING, set_plan_status

logger = logging.getLogger(__name__)

_ENTERED_MESSAGE = (
    "Plan mode is active. Stay read-only: research the codebase, then record your "
    "implementation plan with the `save_plan` tool (it publishes the plan to the "
    "review page) and share the plan-review link in the source channel. Do not edit "
    "files, commit, push, or open a PR — wait for the user to approve the plan."
)


def enter_plan_mode(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Activate plan mode mid-run.

    Call this when you believe the task would benefit from a structured
    implementation plan before writing any code — e.g. when the request is
    complex, touches many files, or has multiple valid approaches. This is
    NOT triggered by the word "plan" appearing in the request; use your
    judgment about whether planning is genuinely warranted.

    Once activated, stay read-only: research the codebase, then record your plan
    with the ``save_plan`` tool (it publishes the plan to the review page) and
    share the plan-review link with the user. Do not edit files, commit, push,
    or open a PR — the user reviews the plan and approves it before you
    implement.
    """
    thread_id = _thread_id_from_config()
    if thread_id:
        try:
            asyncio.run(set_plan_status(thread_id, PLAN_STATUS_PLANNING, plan_mode=True))
        except Exception:
            logger.warning("Failed to persist plan-mode entry for %s", thread_id, exc_info=True)
    return Command(
        update={
            "plan_mode": True,
            "messages": [ToolMessage(content=_ENTERED_MESSAGE, tool_call_id=tool_call_id)],
        }
    )


def _thread_id_from_config() -> str | None:
    try:
        config = get_config()
    except Exception:
        return None
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return str(thread_id) if thread_id else None
