"""Persistence for the plan-review feature.

The plan lives in two places:
  - the agent's sandbox, as a real ``plan.md`` file (written by the ``save_plan``
    tool — the source artifact the agent produces and can re-read), and
  - the LangGraph store, as the published snapshot the dashboard renders.

Reviewers leave whole-document comments, stored one item per comment under
``["plan", "comments", thread_id]`` so listing and deletion are simple plain
store operations (no CRDT/WebSocket).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

PLAN_CONTENT_NAMESPACE = ["plan", "content"]
PLAN_COMMENTS_NAMESPACE = ["plan", "comments"]

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


def _comments_namespace(thread_id: str) -> list[str]:
    return [*PLAN_COMMENTS_NAMESPACE, thread_id]


async def list_plan_comments(thread_id: str) -> list[dict[str, Any]]:
    """All comments on a plan, oldest first."""
    client = _client()
    try:
        items = await client.store.search_items(_comments_namespace(thread_id), limit=1000)
    except Exception:
        return []
    raw = items.get("items", []) if isinstance(items, dict) else getattr(items, "items", [])
    comments = [v for v in (_item_value(item) for item in raw) if v]
    comments.sort(key=lambda c: str(c.get("created_at", "")))
    return comments


async def add_plan_comment(
    thread_id: str, *, author: str, author_login: str, body: str
) -> dict[str, Any]:
    """Append a whole-document comment; returns the stored comment."""
    comment = {
        "id": uuid.uuid4().hex,
        "author": author,
        "author_login": author_login,
        "body": body,
        "created_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item(_comments_namespace(thread_id), comment["id"], comment)
    return comment


async def delete_plan_comment(thread_id: str, comment_id: str) -> None:
    await _client().store.delete_item(_comments_namespace(thread_id), comment_id)


async def _merge_thread_metadata(thread_id: str, metadata: dict[str, Any]) -> None:
    client = _client()
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata)
    except Exception:
        # The thread always exists by the time a plan is saved (the run created
        # it); a transient update failure must not crash the agent mid-run.
        pass
