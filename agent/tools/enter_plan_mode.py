"""Tool: ``enter_plan_mode``. Switch the run into read-only planning."""

import logging
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.types import Command

from agent.dashboard.plan_store import PLAN_STATUS_PLANNING, set_plan_status
from coding_agent.run_config import RunConfig

logger = logging.getLogger(__name__)

_ENTERED_MESSAGE = (
    "Plan mode is active. Stay read-only for the target repo: research the codebase, "
    "create or edit a dated, self-contained HTML artifact under `/workspace/plans/`, then "
    "publish it with the `save_plan` tool and share the plan-review link in the source "
    "channel. Do not edit repo files, commit, push, or open a PR — wait for the user to "
    "approve the plan."
)


async def enter_plan_mode(
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Implement the `enter_plan_mode` tool."""
    thread_id = _thread_id_from_config()
    if thread_id:
        try:
            await set_plan_status(thread_id, PLAN_STATUS_PLANNING, plan_mode=True)
        except Exception:
            logger.warning("Failed to persist plan-mode entry for %s", thread_id, exc_info=True)
    return Command(
        update={
            "plan_mode": True,
            "messages": [ToolMessage(content=_ENTERED_MESSAGE, tool_call_id=tool_call_id)],
        }
    )


def _thread_id_from_config() -> str | None:
    return RunConfig.from_runtime().thread_id or None
