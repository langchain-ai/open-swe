"""Event-driven delivery of Slack completion messages after agent PRs turn green."""

import importlib
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from langgraph_sdk import get_client
from langgraph_sdk.errors import ConflictError
from pydantic import BaseModel, ConfigDict

from .dashboard.pull_request_context import _fetch_checks
from .source_context import SourceContext
from .store import TypedStore, now_iso
from .thread_ids import pr_completion_lock_thread_id
from .utils.github_app import get_github_app_installation_token
from .utils.github_ci import branch_from_check_payload, fetch_pr, head_sha_from_check_payload
from .utils.github_http import github_client

logger = logging.getLogger(__name__)

WATCH_NAMESPACE = ["pr_completion_watches"]
WATCH_LOCK_TTL_MINUTES = 5


class PRCompletionWatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    active: bool = True
    thread_id: str
    owner: str
    repo: str
    pr_number: int
    pr_url: str
    head_sha: str
    head_ref: str
    installation_id: int
    source_context: SourceContext
    deferred_message: str = ""
    green_head_sha: str = ""
    created_at: str
    updated_at: str


class PRCompletionWatchStore(TypedStore[PRCompletionWatch]):
    def __init__(self) -> None:
        super().__init__(WATCH_NAMESPACE, PRCompletionWatch)

    async def save(self, watch: PRCompletionWatch) -> PRCompletionWatch:
        watch.updated_at = now_iso()
        return await self.put(watch.key, watch)

    async def list_active(
        self,
        *,
        thread_id: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
    ) -> list[PRCompletionWatch]:
        filters: dict[str, Any] = {"active": True}
        if thread_id:
            filters["thread_id"] = thread_id
        if owner:
            filters["owner"] = owner.lower()
        if repo:
            filters["repo"] = repo.lower()
        return await self.search_all(filter=filters)


WATCHES = PRCompletionWatchStore()


def watch_key(thread_id: str) -> str:
    return thread_id


@asynccontextmanager
async def _watch_lock(key: str) -> AsyncIterator[bool]:
    client = get_client()
    lock_id = pr_completion_lock_thread_id(key)
    try:
        await client.threads.create(
            thread_id=lock_id, if_exists="raise", ttl=WATCH_LOCK_TTL_MINUTES
        )
    except ConflictError:
        yield False
        return
    except Exception:
        logger.warning("Failed to acquire PR completion lock for %s", key, exc_info=True)
        yield False
        return
    try:
        yield True
    finally:
        try:
            await client.threads.delete(lock_id)
        except Exception:
            logger.warning("Failed to release PR completion lock for %s", key, exc_info=True)


async def arm_watch(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str,
    head_sha: str,
    head_ref: str,
    installation_id: int,
    source_context: SourceContext,
) -> PRCompletionWatch:
    now = now_iso()
    watch = PRCompletionWatch(
        key=watch_key(thread_id),
        thread_id=thread_id,
        owner=owner.lower(),
        repo=repo.lower(),
        pr_number=pr_number,
        pr_url=pr_url,
        head_sha=head_sha,
        head_ref=head_ref,
        installation_id=installation_id,
        source_context=source_context,
        created_at=now,
        updated_at=now,
    )
    return await WATCHES.save(watch)


async def defer_message(thread_id: str, message: str) -> PRCompletionWatch | None:
    watches = await WATCHES.list_active(thread_id=thread_id)
    if not watches:
        return None
    watch = max(watches, key=lambda item: item.updated_at)
    async with _watch_lock(watch.key) as acquired:
        if not acquired:
            raise RuntimeError("PR completion watch is busy; message was not deferred")
        current = await WATCHES.get(watch.key)
        if not current or not current.active:
            return None
        current.deferred_message = message.strip()
        if current.green_head_sha == current.head_sha:
            await _notify_watch(current)
            return current
        return await WATCHES.save(current)


def _is_green(checks: Mapping[str, Any], head_sha: str) -> bool:
    if checks.get("headSha") != head_sha or checks.get("rollupState") != "SUCCESS":
        return False
    if checks.get("truncated") is not False:
        return False
    actionable = checks.get("checks")
    return isinstance(actionable, list) and not actionable


def _is_success_event(payload: Mapping[str, Any], event_type: str) -> bool:
    if event_type in {"check_run", "check_suite", "workflow_run"}:
        node = payload.get(event_type)
        return (
            isinstance(node, Mapping)
            and node.get("status") == "completed"
            and node.get("conclusion") in {"success", "neutral", "skipped"}
        )
    return event_type == "status" and payload.get("state") == "success"


async def _evaluate_watch(watch: PRCompletionWatch) -> bool:
    token = await get_github_app_installation_token(installation_id=watch.installation_id)
    if not token:
        return False
    pr = await fetch_pr(
        owner=watch.owner,
        repo=watch.repo,
        pr_number=watch.pr_number,
        token=token,
    )
    if not pr or pr.get("state") != "open":
        return False
    head = pr.get("head")
    current_sha = head.get("sha") if isinstance(head, Mapping) else None
    if current_sha != watch.head_sha:
        if isinstance(current_sha, str) and current_sha:
            watch.head_sha = current_sha
            watch.green_head_sha = ""
            current_ref = head.get("ref") if isinstance(head, Mapping) else None
            if isinstance(current_ref, str) and current_ref:
                watch.head_ref = current_ref
            await WATCHES.save(watch)
        return False
    async with github_client(token=token) as client:
        checks = await _fetch_checks(client, watch.owner, watch.repo, watch.pr_number)
    if checks is None or not _is_green(checks, watch.head_sha):
        return False
    watch.green_head_sha = watch.head_sha
    if not watch.deferred_message:
        await WATCHES.save(watch)
        return False
    return await _notify_watch(watch)


async def _notify_watch(watch: PRCompletionWatch) -> bool:
    post_slack_thread_reply = importlib.import_module("agent.utils.slack").post_slack_thread_reply
    location = watch.source_context.slack_location
    if location is None:
        return False
    thread_ts = location[1]
    slack_thread = watch.source_context.slack_thread
    reply_thread_ts = (
        (slack_thread.model_extra or {}).get("reply_thread_ts") if slack_thread else None
    )
    if thread_ts == "0" and isinstance(reply_thread_ts, str) and reply_thread_ts:
        thread_ts = reply_thread_ts
    message = watch.deferred_message
    if watch.pr_url not in message:
        message = f"{message.rstrip()} {watch.pr_url}"
    posted = await post_slack_thread_reply(
        location[0],
        thread_ts,
        message,
        agent_thread_id=watch.thread_id,
        bypass_pr_completion_gate=True,
    )
    if posted:
        await WATCHES.delete(watch.key)
    return posted


async def handle_ci_webhook(payload: dict[str, Any], event_type: str) -> dict[str, int]:
    if not _is_success_event(payload, event_type):
        return {"matched": 0, "notified": 0}
    repository = payload.get("repository")
    owner_node = repository.get("owner") if isinstance(repository, Mapping) else None
    owner = owner_node.get("login") if isinstance(owner_node, Mapping) else None
    repo = repository.get("name") if isinstance(repository, Mapping) else None
    head_sha = head_sha_from_check_payload(payload, event_type)
    branch = branch_from_check_payload(payload, event_type)
    if not isinstance(owner, str) or not owner or not isinstance(repo, str) or not repo:
        return {"matched": 0, "notified": 0}
    watches = [
        watch
        for watch in await WATCHES.list_active(owner=owner, repo=repo)
        if watch.head_sha == head_sha or (branch and watch.head_ref == branch)
    ]
    notified = 0
    for watch in watches:
        async with _watch_lock(watch.key) as acquired:
            if not acquired:
                continue
            current = await WATCHES.get(watch.key)
            if current and current.active and await _evaluate_watch(current):
                notified += 1
    return {"matched": len(watches), "notified": notified}
