"""Record the structured result of a scheduled automation run."""

from typing import Any

from langchain_core.tools import tool
from langgraph_sdk import get_client

from agent.run_config import RunConfig

_MAX_SUMMARY_CHARS = 4_000


@tool
async def record_automation_outcome(
    blocker_keys: list[str], outcome_summary: str, action_taken: bool
) -> dict[str, Any]:
    """Record the complete blocker set and outcome before a scheduled run ends."""
    cfg = RunConfig.from_runtime()
    if cfg.source != "schedule":
        return {"success": False, "error": "This tool is only available to scheduled runs"}
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "Missing scheduled thread ID"}
    clean_keys = sorted(
        {key.strip() for key in blocker_keys if isinstance(key, str) and key.strip()}
    )
    summary = outcome_summary.strip()
    if not summary:
        return {"success": False, "error": "Outcome summary cannot be empty"}
    if len(summary) > _MAX_SUMMARY_CHARS:
        return {
            "success": False,
            "error": f"Outcome summary must be at most {_MAX_SUMMARY_CHARS} characters",
        }
    outcome = {
        "blocker_keys": clean_keys,
        "outcome_summary": summary,
        "action_taken": action_taken,
        "run_id": cfg.run_id or cfg.prepare_run_id,
    }
    await get_client().threads.update(
        thread_id=thread_id,
        metadata={"automation_outcome": outcome},
    )
    return {"success": True, "outcome": outcome}
