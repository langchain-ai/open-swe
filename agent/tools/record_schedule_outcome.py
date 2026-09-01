from typing import Any

from langgraph.config import get_config


async def record_schedule_outcome(
    schedule_id: str,
    verdict: str,
    counted_signatures: list[str],
    resolving_pr: str | None = None,
    notified_signatures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist the outcome digest for a scheduled run."""
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if not isinstance(configurable, dict) or configurable.get("source") != "schedule":
        return {"success": False, "error": "This tool is only available to scheduled runs"}
    configured_schedule_id = configurable.get("schedule_id")
    if configured_schedule_id != schedule_id:
        return {"success": False, "error": "schedule_id does not match the current run"}
    from ..dashboard.schedules import record_schedule_outcome as persist_outcome

    return await persist_outcome(
        schedule_id,
        verdict,
        counted_signatures,
        resolving_pr,
        notified_signatures,
    )
