"""Persistence for the collaborative plan-review feature.

The plan lives in two places:
  - the agent's sandbox, as a real ``plan.md`` file (written by the ``save_plan``
    tool — the source artifact the agent produces and can re-read), and
  - the LangGraph store, as the published snapshot the dashboard renders and the
    seed for the collaborative Yjs document.

Comment threads live in the Yjs document (BlockNote's ``YjsThreadStore``); the
binary Yjs state is snapshotted here so a plan + its comments survive a sandbox
teardown or a server restart.
"""

from __future__ import annotations

import base64
from typing import Any

from langgraph_sdk import get_client

PLAN_CONTENT_NAMESPACE = ["plan", "content"]
PLAN_YJS_NAMESPACE = ["plan", "yjs"]

# Plan lifecycle, stored on both the content record and the thread metadata.
PLAN_STATUS_PLANNING = "planning"
PLAN_STATUS_READY = "ready"
PLAN_STATUS_REVISING = "revising"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_CANCELLED = "cancelled"


def _client() -> Any:
    return get_client()


def _item_value(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def save_plan_content(
    thread_id: str, *, markdown: str, status: str = PLAN_STATUS_READY
) -> None:
    """Publish the plan markdown + status for the dashboard to render."""
    client = _client()
    await client.store.put_item(
        PLAN_CONTENT_NAMESPACE,
        thread_id,
        {"markdown": markdown, "status": status},
    )
    await _merge_thread_metadata(thread_id, {"plan_status": status, "plan_mode": True})


async def get_plan_content(thread_id: str) -> dict[str, Any] | None:
    client = _client()
    try:
        item = await client.store.get_item(PLAN_CONTENT_NAMESPACE, thread_id)
    except Exception:
        return None
    return _item_value(item)


async def set_plan_status(thread_id: str, status: str, *, plan_mode: bool | None = None) -> None:
    """Update the plan lifecycle status on both the content record and metadata."""
    existing = await get_plan_content(thread_id) or {}
    client = _client()
    await client.store.put_item(
        PLAN_CONTENT_NAMESPACE,
        thread_id,
        {"markdown": existing.get("markdown", ""), "status": status},
    )
    metadata: dict[str, Any] = {"plan_status": status}
    if plan_mode is not None:
        metadata["plan_mode"] = plan_mode
    await _merge_thread_metadata(thread_id, metadata)


async def load_yjs_snapshot(thread_id: str) -> bytes | None:
    client = _client()
    try:
        item = await client.store.get_item(PLAN_YJS_NAMESPACE, thread_id)
    except Exception:
        return None
    value = _item_value(item)
    if not value:
        return None
    encoded = value.get("b64")
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError):
        return None


async def save_yjs_snapshot(thread_id: str, data: bytes) -> None:
    client = _client()
    await client.store.put_item(
        PLAN_YJS_NAMESPACE,
        thread_id,
        {"b64": base64.b64encode(data).decode("ascii")},
    )


async def _merge_thread_metadata(thread_id: str, metadata: dict[str, Any]) -> None:
    client = _client()
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata)
    except Exception:
        # The thread always exists by the time a plan is saved (the run created
        # it); a transient update failure must not crash the agent mid-run.
        pass
