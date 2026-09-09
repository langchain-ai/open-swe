"""Best-effort feedback prompts after five quiet minutes."""

import logging
from asyncio import Task, create_task, sleep
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from agent.source_context import SourceContext
from agent.store import TypedStore, now_ms
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)
DELAY_MS = 5 * 60 * 1000
ACTIVITY_KEY = "feedback_last_activity_at_ms"
_pending_tasks: set[Task[None]] = set()
Rating = Literal["bad", "good", "other"]


class Feedback(BaseModel):
    status: Literal["pending", "ready", "completed", "dismissed"] = "pending"
    event_id: str = ""
    answer_run_id: str = ""
    activity_at_ms: int = 0
    rating: Rating | None = None
    comment: str = ""


def feedback_store() -> TypedStore[Feedback]:
    return TypedStore(("thread_feedback",), Feedback)


async def complete_feedback_prompt(
    thread_id: str, status: Literal["completed", "dismissed"]
) -> None:
    try:
        record = await feedback_store().get(thread_id) or Feedback()
        record.status = status
        await feedback_store().put(thread_id, record)
    except Exception:
        logger.warning("Could not complete feedback prompt", extra={"thread_id": thread_id})


async def note_feedback_activity(thread_id: str, *, client: Any = None) -> None:
    try:
        await (client or langgraph_client()).threads.update(
            thread_id=thread_id, metadata={ACTIVITY_KEY: now_ms()}
        )
    except Exception:
        logger.warning("Could not record feedback activity", extra={"thread_id": thread_id})


async def _quiet_until(thread_id: str, record: Feedback) -> int | None:
    client = langgraph_client()
    thread = await client.threads.get(thread_id)
    activity = (thread.get("metadata") or {}).get(ACTIVITY_KEY, 0)
    runs = await client.runs.list(thread_id, limit=1)
    latest = runs[0] if runs else {}
    if record.answer_run_id and (
        latest.get("run_id") != record.answer_run_id or activity > record.activity_at_ms
    ):
        return None
    if thread.get("status") == "busy" or latest.get("status") in {"pending", "running"}:
        return now_ms() + DELAY_MS
    if latest and latest.get("status") != "success":
        return None
    finished_at = latest.get("updated_at")
    if finished_at:
        activity = max(activity, int(datetime.fromisoformat(finished_at).timestamp() * 1000))
    return activity + DELAY_MS


async def feedback_prompt_status(thread_id: str) -> str:
    record = await feedback_store().get(thread_id)
    if record is None or record.status == "pending":
        return "unavailable"
    if record.status == "ready":
        due = await _quiet_until(thread_id, record)
        if due is None or due > now_ms():
            return "unavailable"
    return record.status


async def feedback_event_is_ready(thread_id: str, event_id: str) -> bool:
    record = await feedback_store().get(thread_id)
    return bool(
        record
        and record.event_id == event_id
        and await feedback_prompt_status(thread_id) == "ready"
    )


async def _wait_for_feedback(
    thread_id: str, record: Feedback, channel_id: str, slack_run_id: str
) -> None:
    due = now_ms() + DELAY_MS
    try:
        while True:
            await sleep(max(0, (due - now_ms()) / 1000))
            async with agent_thread_pr_state_lock(langgraph_client(), thread_id):
                current = await feedback_store().get(thread_id)
                if current != record:
                    return
                due = await _quiet_until(thread_id, record)
                if due is None:
                    return
                if due > now_ms():
                    continue
                record.status = "ready"
                await feedback_store().put(thread_id, record)
            if channel_id and slack_run_id:
                from agent.slack.thread_feedback import post_slack_feedback_prompt

                await post_slack_feedback_prompt(
                    thread_id, slack_run_id, channel_id, expected_event_id=record.event_id
                )
            return
    except Exception:
        logger.warning("Could not deliver feedback prompt", extra={"thread_id": thread_id})


async def _schedule(
    thread_id: str,
    metadata: dict[str, Any],
    *,
    event_id: str,
    answer_run_id: str = "",
    channel_id: str = "",
    slack_run_id: str = "",
) -> None:
    record = Feedback(
        event_id=event_id, answer_run_id=answer_run_id, activity_at_ms=metadata.get(ACTIVITY_KEY, 0)
    )
    try:
        if answer_run_id and await _quiet_until(thread_id, record) is None:
            return
        async with agent_thread_pr_state_lock(langgraph_client(), thread_id):
            current = await feedback_store().get(thread_id)
            if current and (
                current.status in {"completed", "dismissed"}
                or (
                    current.event_id == event_id and current.activity_at_ms >= record.activity_at_ms
                )
                or (answer_run_id and current.event_id.startswith("merged_pr:"))
            ):
                return
            await feedback_store().put(thread_id, record)
            task = create_task(_wait_for_feedback(thread_id, record, channel_id, slack_run_id))
            _pending_tasks.add(task)
            task.add_done_callback(_pending_tasks.discard)
    except Exception:
        logger.warning("Could not schedule feedback prompt", extra={"thread_id": thread_id})


async def mark_answered_question(thread_id: str, run_id: str) -> None:
    thread = await langgraph_client().threads.get(thread_id)
    await _schedule(
        thread_id, thread.get("metadata") or {}, event_id=f"answer:{run_id}", answer_run_id=run_id
    )


async def schedule_answer_feedback(thread_id: str, run_id: str, metadata: dict[str, Any]) -> None:
    from agent.slack.client import lookup_slack_run_message_mapping

    origin = SourceContext.from_metadata(metadata).slack_thread
    if origin is None:
        return
    mapping = await lookup_slack_run_message_mapping(langgraph_client(), origin.channel_id, run_id)
    if (
        mapping
        and mapping.get("run_id") == run_id
        and mapping.get("should_ask_for_feedback") is True
    ):
        await _schedule(
            thread_id,
            metadata,
            event_id=f"answer:{run_id}",
            answer_run_id=run_id,
            channel_id=origin.channel_id,
            slack_run_id=run_id,
        )


async def schedule_pr_feedback(thread_id: str, metadata: dict[str, Any], pr_url: str) -> None:
    record = next(
        (pr for pr in (metadata.get("pull_requests") or []) if pr.get("url") == pr_url), {}
    )
    origin = record.get("slack_feedback") or {}
    await _schedule(
        thread_id,
        metadata,
        event_id=f"merged_pr:{pr_url}",
        channel_id=origin.get("channel_id", ""),
        slack_run_id=origin.get("run_id", ""),
    )
