"""Tool: submit a delivery worker result to the queue."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from ..delivery_results import ingest_delivery_worker_result
from ..delivery_review import launch_delivery_review_checks

logger = logging.getLogger(__name__)


def _configurable() -> dict[str, Any]:
    try:
        config = get_config()
    except Exception:
        config = {}
    configurable = config.get("configurable") if isinstance(config, dict) else {}
    return dict(configurable) if isinstance(configurable, Mapping) else {}


async def submit_delivery_worker_result(result: dict[str, Any]) -> dict[str, Any]:
    """Submit the finished delivery worker result and start review when accepted.

    Use this only after the implementation branch and pull request are ready.
    The result must include cause, changed_files, before_proof, after_proof,
    executed_gates, risks, pull_request_summary, PR details, and QA evidence.
    """
    configurable = _configurable()
    item_id = configurable.get("delivery_queue_item_id")
    if not isinstance(item_id, str) or not item_id.strip():
        return {
            "success": False,
            "error": "delivery_queue_item_id is missing from run config",
        }

    try:
        updated = await ingest_delivery_worker_result(item_id.strip(), result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to submit delivery worker result for %s", item_id)
        return {"success": False, "error": str(exc)}

    review_launch = None
    if updated.get("status") == "review":
        review_launch = await launch_delivery_review_checks(item_id.strip())

    return {
        "success": updated.get("status") == "review"
        and (not review_launch or review_launch.get("status") == "launched"),
        "item_id": item_id.strip(),
        "queue_status": updated.get("status"),
        "blockers": updated.get("blockers") or [],
        "review_launch": review_launch,
    }
