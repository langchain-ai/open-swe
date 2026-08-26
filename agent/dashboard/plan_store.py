"""Persistence for the plan-review feature.

The plan lives in two places:
  - the agent's sandbox, as a self-contained HTML file the agent creates and edits, and
  - the LangGraph store, as the published snapshot the dashboard renders.

Reviewers leave whole-document comments, stored one item per comment under
``["plan", "comments", thread_id]`` so listing and deletion are simple plain
store operations (no CRDT/WebSocket).
"""

import html
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

PLAN_CONTENT_NAMESPACE = ["plan", "content"]
PLAN_COMMENTS_NAMESPACE = ["plan", "comments"]

# Plans are mirrored into the sandbox outside cloned repositories.
PLAN_FILE_DIRECTORY = "/workspace/plans"

# Plan/share lifecycle, stored on both the content record and the thread metadata.
PLAN_STATUS_PLANNING = "planning"
PLAN_STATUS_READY = "ready"
PLAN_STATUS_SHARED = "shared"
PLAN_STATUS_REVISING = "revising"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_CANCELLED = "cancelled"


def make_plan_approver(*, actor_id: str, name: str, source: str) -> dict[str, str]:
    actor_id = actor_id.strip()
    name = name.strip()
    source = source.strip()
    return {
        "id": actor_id or name or "unknown",
        "name": name or actor_id or "Unknown user",
        "source": source or "unknown",
    }


def plan_file_path_for_thread(thread_id: str) -> str:
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", thread_id).strip("-").lower()[:48]
    return f"{PLAN_FILE_DIRECTORY}/{date}-{slug or 'plan'}.html"


def _client() -> Any:
    return get_client()


def _item_value(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _stored_plan_file_path(client: Any, thread_id: str) -> str | None:
    try:
        value = _item_value(await client.store.get_item(PLAN_CONTENT_NAMESPACE, thread_id)) or {}
    except Exception:
        return None
    path = value.get("plan_file_path")
    return path if isinstance(path, str) and path else None


async def save_plan_content(
    thread_id: str,
    *,
    html: str | None = None,
    markdown: str | None = None,
    status: str = PLAN_STATUS_READY,
    clear_comments: bool = True,
    plan_file_path: str | None = None,
    plan_mode: bool | None = True,
) -> None:
    """Publish HTML + status for the dashboard to render.

    A republished (revised) plan supersedes the prior revision, so comments left
    on it are cleared — otherwise stale feedback would resurface on the new plan
    and be fed back to the agent on the next approve/reject. A manual owner edit
    passes ``clear_comments=False`` so reviewer feedback survives the edit."""
    client = _client()
    if plan_file_path is None:
        plan_file_path = await _stored_plan_file_path(client, thread_id)
    record: dict[str, Any] = {"status": status}
    if html is not None:
        record["html"] = html
    if markdown is not None:
        record["markdown"] = markdown
    if plan_file_path:
        record["plan_file_path"] = plan_file_path
    await client.store.put_item(
        PLAN_CONTENT_NAMESPACE,
        thread_id,
        record,
    )
    if clear_comments:
        try:
            await clear_plan_comments(thread_id)
        except Exception:
            # Best-effort: a failed cleanup must not block publishing the new plan.
            pass
    metadata: dict[str, Any] = {"plan_status": status}
    if plan_mode is not None:
        metadata["plan_mode"] = plan_mode
    await _merge_thread_metadata(thread_id, metadata)


async def write_plan_to_sandbox(
    thread_id: str, content: str, *, plan_file_path: str | None = None
) -> str:
    """Mirror the dashboard plan edit into the thread's sandbox.

    Best-effort: a missing sandbox must not block publishing the plan to the
    review page.
    """
    path = plan_file_path or plan_file_path_for_thread(thread_id)
    try:
        from ..utils.sandbox_state import get_sandbox_backend

        backend = await get_sandbox_backend(thread_id)
        await backend.awrite(path, content)
        return path
    except Exception:
        logger.warning("Could not write plan file to sandbox for %s", thread_id, exc_info=True)
        return path


async def get_plan_content(
    thread_id: str, *, raise_on_error: bool = False
) -> dict[str, Any] | None:
    """The published plan record, or ``None`` when none exists.

    With ``raise_on_error=True`` a store failure propagates instead of resolving
    to ``None``. Approve uses this so a transient failure aborts the decision
    rather than dispatching the agent without the (possibly edited) plan."""
    client = _client()
    try:
        item = await client.store.get_item(PLAN_CONTENT_NAMESPACE, thread_id)
    except Exception:
        if raise_on_error:
            raise
        return None
    return _item_value(item)


async def set_plan_status(
    thread_id: str,
    status: str,
    *,
    plan_mode: bool | None = None,
    approved_by: Mapping[str, str] | None = None,
) -> None:
    """Update the plan lifecycle status on both the content record and metadata."""
    existing = await get_plan_content(thread_id) or {}
    entering_plan_after_share = (
        existing.get("status") == PLAN_STATUS_SHARED and status == PLAN_STATUS_PLANNING
    )
    client = _client()
    record: dict[str, Any] = {"status": status}
    if not entering_plan_after_share:
        for field in ("html", "markdown"):
            value = existing.get(field)
            if isinstance(value, str):
                record[field] = value
    else:
        record["html"] = ""
    plan_file_path = existing.get("plan_file_path")
    if not entering_plan_after_share and isinstance(plan_file_path, str) and plan_file_path:
        record["plan_file_path"] = plan_file_path
    metadata: dict[str, Any] = {"plan_status": status}
    if status == PLAN_STATUS_APPROVED and approved_by is not None:
        approver = make_plan_approver(
            actor_id=str(approved_by.get("id") or ""),
            name=str(approved_by.get("name") or ""),
            source=str(approved_by.get("source") or ""),
        )
        approved_at = datetime.now(UTC).isoformat()
        record.update(approved_by=approver, approved_at=approved_at)
        metadata.update(plan_approved_by=approver, plan_approved_at=approved_at)
    await client.store.put_item(
        PLAN_CONTENT_NAMESPACE,
        thread_id,
        record,
    )
    if plan_mode is not None:
        metadata["plan_mode"] = plan_mode
    await _merge_thread_metadata(thread_id, metadata)


def _comments_namespace(thread_id: str) -> list[str]:
    return [*PLAN_COMMENTS_NAMESPACE, thread_id]


def format_plan_comments(comments: list[dict[str, Any]]) -> str:
    entries: list[str] = []
    for comment in comments:
        body = str(comment.get("body", "")).strip()
        if not body:
            continue
        author = html.escape(str(comment.get("author") or "reviewer").strip(), quote=True)
        anchor = comment.get("anchor")
        anchor = anchor if isinstance(anchor, dict) else {}
        exact = str(anchor.get("exact") or "").strip()
        prefix = str(anchor.get("context_before") or anchor.get("prefix") or "").strip()
        suffix = str(anchor.get("context_after") or anchor.get("suffix") or "").strip()
        context = "\n".join(part for part in (prefix, exact, suffix) if part)
        fields = []
        if context:
            fields.append(f"<surrounding-context>{html.escape(context)}</surrounding-context>")
        if exact:
            fields.append(f"<highlighted-text>{html.escape(exact)}</highlighted-text>")
        fields.append(f"<reviewer-feedback>{html.escape(body)}</reviewer-feedback>")
        entries.append(
            f'{len(entries) + 1}. <plan-review-comment author="{author}">\n'
            + "\n".join(fields)
            + "\n</plan-review-comment>"
        )
    return "\n".join(entries)


async def list_plan_comments(
    thread_id: str, *, raise_on_error: bool = False
) -> list[dict[str, Any]]:
    """All comments on a plan, oldest first.

    With ``raise_on_error=True`` a store/search failure propagates instead of
    resolving to ``[]``. Approve/reject use this so a transient failure surfaces
    (the decision endpoint errors) rather than silently feeding the agent an
    empty comment set and dropping the reviewer's feedback."""
    client = _client()
    try:
        items = await client.store.search_items(_comments_namespace(thread_id), limit=1000)
    except Exception:
        if raise_on_error:
            raise
        return []
    raw = items.get("items", []) if isinstance(items, dict) else getattr(items, "items", [])
    comments = [v for v in (_item_value(item) for item in raw) if v]
    comments.sort(key=lambda c: str(c.get("created_at", "")))
    return comments


async def clear_plan_comments(thread_id: str) -> None:
    """Delete every comment on a thread (called when a revised plan is published)."""
    for comment in await list_plan_comments(thread_id):
        comment_id = comment.get("id")
        if isinstance(comment_id, str) and comment_id:
            await delete_plan_comment(thread_id, comment_id)


async def add_plan_comment(
    thread_id: str,
    *,
    author: str,
    author_login: str,
    body: str,
    anchor: dict[str, Any] | None,
) -> dict[str, Any]:
    """Append an anchored comment; returns the stored comment."""
    comment = {
        "id": uuid.uuid4().hex,
        "author": author,
        "author_login": author_login,
        "body": body,
        "anchor": anchor,
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
