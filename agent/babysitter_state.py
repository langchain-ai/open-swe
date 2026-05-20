"""Thread metadata helpers for the PR babysitter agent."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph_sdk import get_client

BABYSITTER_THREAD_KIND = "babysitter"


class BabysitterPRMeta(TypedDict, total=False):
    owner: str
    name: str
    number: int
    url: str
    title: str
    head_ref: str
    base_ref: str
    head_sha: str
    base_sha: str


async def set_babysitter_thread_metadata(
    thread_id: str,
    *,
    pr: BabysitterPRMeta | None = None,
    watch: bool | None = None,
    last_checked_at: str | None = None,
    last_seen_head_sha: str | None = None,
    last_action_key: str | None = None,
    fix_attempt_count: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist babysitter-thread metadata with merge semantics."""
    client = get_client()
    metadata: dict[str, Any] = {"kind": BABYSITTER_THREAD_KIND}
    if pr is not None:
        metadata["pr"] = pr
    if watch is not None:
        metadata["watch"] = watch
    if last_checked_at is not None:
        metadata["last_checked_at"] = last_checked_at
    if last_seen_head_sha is not None:
        metadata["last_seen_head_sha"] = last_seen_head_sha
    if last_action_key is not None:
        metadata["last_action_key"] = last_action_key
    if fix_attempt_count is not None:
        metadata["fix_attempt_count"] = fix_attempt_count
    if extra:
        metadata.update(extra)
    await client.threads.update(thread_id=thread_id, metadata=metadata)
