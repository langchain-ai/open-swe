"""Tools: submit delivery review and QA results."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from ..dashboard.provider_pat_vault import resolve_provider_pat
from ..delivery_merge import execute_delivery_merge
from ..delivery_queue import read_delivery_queue_item, transition_delivery_queue_status
from ..delivery_review import record_delivery_review_result

logger = logging.getLogger(__name__)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _configurable() -> dict[str, Any]:
    try:
        config = get_config()
    except Exception:
        config = {}
    configurable = config.get("configurable") if isinstance(config, dict) else {}
    return dict(configurable) if isinstance(configurable, Mapping) else {}


def _delivery_queue_item_id() -> str:
    item_id = _configurable().get("delivery_queue_item_id")
    return _string(item_id)


def _login_from_identity(identity: str, provider: str) -> str:
    parts = [part.strip() for part in identity.split(":") if part.strip()]
    if len(parts) >= 3 and parts[0].lower() == provider and parts[1].lower() == "user":
        return parts[2]
    if len(parts) >= 2 and parts[0].lower() == provider:
        return parts[-1]
    return ""


async def _resolve_merge_token(item: Mapping[str, Any]) -> str | None:
    credential_policy = _mapping(item.get("credential_policy"))
    provider = _string(credential_policy.get("provider")).lower() or "github"
    identity = _string(item.get("merge_credential_identity") or item.get("credential_identity"))
    login = _login_from_identity(identity, provider)
    if not login:
        return None
    resolved = await resolve_provider_pat(
        login,
        provider=provider,
        project_id=_string(item.get("project_id")),
        action="merge",
    )
    return resolved.token if resolved is not None else None


async def _merge_when_eligible(item_id: str, item: Mapping[str, Any]) -> dict[str, Any] | None:
    merge_policy = _mapping(item.get("merge_policy"))
    if item.get("merge_eligible") is not True or merge_policy.get("enabled") is not True:
        return None
    token = await _resolve_merge_token(item)
    return await execute_delivery_merge(item_id, token=token)


async def _record_review_and_maybe_merge(
    item_id: str,
    review: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = await record_delivery_review_result(item_id, review)
    merged = await _merge_when_eligible(item_id, reviewed)
    final_item = merged or reviewed
    return {
        "success": final_item.get("status") in {"review", "done"},
        "item_id": item_id,
        "queue_status": final_item.get("status"),
        "merge_status": final_item.get("merge_status"),
        "merge_result": merged,
        "blockers": final_item.get("blockers") or [],
    }


async def submit_delivery_review_result(review: dict[str, Any]) -> dict[str, Any]:
    """Submit the independent delivery review result.

    If a separate QA result is required but not available yet, the review is
    stored and the queue item remains in review until QA submits.
    """
    item_id = _delivery_queue_item_id()
    if not item_id:
        return {
            "success": False,
            "error": "delivery_queue_item_id is missing from run config",
        }
    item = await read_delivery_queue_item(item_id)
    if item is None:
        return {"success": False, "error": f"delivery queue item not found: {item_id}"}

    qa_result = _mapping(review.get("qa_result")) or _mapping(item.get("delivery_qa_result"))
    if _string(item.get("qa_thread_id")) and not qa_result:
        updated = await transition_delivery_queue_status(
            item_id,
            "review",
            reason="waiting_for_qa_result",
            extra={
                "delivery_review_submission": dict(review),
                "review_result": {
                    **_mapping(item.get("review_result")),
                    "status": "waiting_for_qa",
                    "reviewer_thread_id": _string(item.get("reviewer_thread_id")),
                    "qa_thread_id": _string(item.get("qa_thread_id")),
                },
            },
        )
        return {
            "success": True,
            "item_id": item_id,
            "queue_status": updated.get("status"),
            "waiting_for": "qa_result",
        }

    return await _record_review_and_maybe_merge(
        item_id,
        {**dict(review), "qa_result": qa_result} if qa_result else review,
    )


async def submit_delivery_qa_result(qa_result: dict[str, Any]) -> dict[str, Any]:
    """Submit the configured delivery QA result."""
    item_id = _delivery_queue_item_id()
    if not item_id:
        return {
            "success": False,
            "error": "delivery_queue_item_id is missing from run config",
        }
    item = await read_delivery_queue_item(item_id)
    if item is None:
        return {"success": False, "error": f"delivery queue item not found: {item_id}"}

    review_submission = _mapping(item.get("delivery_review_submission"))
    if not review_submission:
        updated = await transition_delivery_queue_status(
            item_id,
            "review",
            reason="waiting_for_review_result",
            extra={
                "delivery_qa_result": dict(qa_result),
                "review_result": {
                    **_mapping(item.get("review_result")),
                    "status": "waiting_for_review",
                    "qa_result": dict(qa_result),
                    "reviewer_thread_id": _string(item.get("reviewer_thread_id")),
                    "qa_thread_id": _string(item.get("qa_thread_id")),
                },
            },
        )
        return {
            "success": True,
            "item_id": item_id,
            "queue_status": updated.get("status"),
            "waiting_for": "review_result",
        }

    return await _record_review_and_maybe_merge(
        item_id,
        {**review_submission, "qa_result": dict(qa_result)},
    )
