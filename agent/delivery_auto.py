"""Auto-Mode delivery queue processing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .delivery_preflight import evaluate_auto_mode_limits
from .delivery_queue import delivery_queue_poll, list_delivery_queue_items
from .delivery_runner import launch_delivery_worker
from .project_registry import get_delivery_project


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _limit(project: Mapping[str, Any], key: str, default: int) -> int:
    run_limits = _mapping(project.get("run_limits"))
    value = run_limits.get(key)
    return value if isinstance(value, int) and value >= 0 else default


def _oldest_first(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: (item.get("created_at") or "", item.get("id") or ""))


async def _project_for_item(item: Mapping[str, Any]) -> dict[str, Any]:
    project_id = _string(item.get("project_id"))
    project = await get_delivery_project(project_id) if project_id else None
    return project or {"project_id": project_id, "run_limits": {}}


async def _active_counts_by_project() -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in ("running", "review"):
        for item in await list_delivery_queue_items({"status": status}):
            project_id = _string(item.get("project_id"))
            counts[project_id] = counts.get(project_id, 0) + 1
    return counts


async def delivery_auto_tick(
    *,
    client: Any | None = None,
    poll: bool = True,
) -> dict[str, Any]:
    poll_result = await delivery_queue_poll() if poll else None
    queued_items = _oldest_first(await list_delivery_queue_items({"status": "queued"}))
    active_counts = await _active_counts_by_project()
    queued_seen_by_project: dict[str, int] = {}
    launched: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in queued_items:
        item_id = _string(item.get("id"))
        project_id = _string(item.get("project_id"))
        project = await _project_for_item(item)

        queued_seen = queued_seen_by_project.get(project_id, 0)
        queued_seen_by_project[project_id] = queued_seen + 1
        if queued_seen >= _limit(project, "max_auto_startable_items", 5):
            skipped.append(
                {
                    "item_id": item_id,
                    "project_id": project_id,
                    "reason": "auto_start_queue_limit",
                }
            )
            continue

        active_count = active_counts.get(project_id, 0)
        if active_count >= _limit(project, "max_concurrent_auto_runs", 1):
            skipped.append(
                {
                    "item_id": item_id,
                    "project_id": project_id,
                    "reason": "auto_active_run_limit",
                }
            )
            continue

        auto_mode = evaluate_auto_mode_limits(
            project,
            active_auto_runs=active_count,
            auto_startable_items=queued_seen,
        )
        result = await launch_delivery_worker(item_id, client=client, auto_mode=auto_mode)
        if result.get("status") == "launched":
            launched.append(result)
            active_counts[project_id] = active_count + 1
        else:
            refused.append(result)

    return {
        "status": "completed",
        "poll": poll_result,
        "queued": len(queued_items),
        "launched": launched,
        "refused": refused,
        "skipped": skipped,
    }
