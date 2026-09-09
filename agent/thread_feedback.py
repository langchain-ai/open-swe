"""Qualify feedback prompts and deliver them after the conversation is quiet."""

import logging
import time
import uuid
from asyncio import Task, create_task, sleep
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from agent.source_context import SourceContext
from agent.store import TypedStore
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)

FEEDBACK_DELAY_MS = 5 * 60 * 1000
ACTIVITY_KEY = "feedback_last_activity_at_ms"
_pending_tasks: set[Task[None]] = set()


class FeedbackPrompt(BaseModel):
    thread_id: str
    run_id: str
    event_id: str
    generation: str
    reason: Literal["answer", "merged_pr"]
    due_at_ms: int
    activity_at_ms: int = 0
    channel_id: str = ""
    slack_run_id: str = ""
    ready: bool = False


class FeedbackCompletion(BaseModel):
    status: Literal["completed", "dismissed"]


def _completions() -> TypedStore[FeedbackCompletion]:
    return TypedStore(("thread_feedback_completions",), FeedbackCompletion)


async def complete_feedback_prompt(
    thread_id: str, status: Literal["completed", "dismissed"]
) -> None:
    try:
        await _completions().put(thread_id, FeedbackCompletion(status=status))
    except Exception:
        logger.warning("Could not complete feedback prompt", extra={"thread_id": thread_id})


def _prompts() -> TypedStore[FeedbackPrompt]:
    return TypedStore(("thread_feedback_prompts",), FeedbackPrompt)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    return 0


async def mark_answered_question(thread_id: str, run_id: str) -> None:
    thread = await langgraph_client().threads.get(thread_id)
    metadata = thread.get("metadata") or {}
    await _schedule(
        thread_id,
        run_id,
        metadata,
        reason="answer",
        event_id=f"answer:{run_id}",
        refresh_answer=True,
    )


async def note_feedback_activity(thread_id: str, *, client: Any = None) -> None:
    try:
        await (client or langgraph_client()).threads.update(
            thread_id=thread_id, metadata={ACTIVITY_KEY: _now_ms()}
        )
    except Exception:
        logger.warning("Could not record feedback activity", extra={"thread_id": thread_id})


async def _schedule(
    thread_id: str,
    run_id: str,
    metadata: dict[str, Any],
    *,
    reason: Literal["answer", "merged_pr"],
    event_id: str,
    channel_id: str = "",
    slack_run_id: str = "",
    refresh_answer: bool = False,
) -> None:
    async with agent_thread_pr_state_lock(langgraph_client(), thread_id):
        if await _completions().get(thread_id) is not None:
            return
        current = await _prompts().get(thread_id)
        if reason == "answer":
            latest = await langgraph_client().runs.list(thread_id, limit=1)
            if latest and latest[0].get("run_id") != run_id:
                return
            if current is not None and current.reason == "merged_pr":
                return
        refreshed = (
            refresh_answer
            and current is not None
            and _timestamp_ms(metadata.get(ACTIVITY_KEY)) > current.activity_at_ms
        )
        if current is not None and current.event_id == event_id and not refreshed:
            if channel_id and slack_run_id and not current.channel_id:
                current.channel_id = channel_id
                current.slack_run_id = slack_run_id
                await _prompts().put(thread_id, current)
            return
        prompt = FeedbackPrompt(
            thread_id=thread_id,
            run_id=run_id,
            event_id=event_id,
            generation=str(uuid.uuid4()),
            reason=reason,
            due_at_ms=_now_ms() + FEEDBACK_DELAY_MS,
            activity_at_ms=_timestamp_ms(metadata.get(ACTIVITY_KEY)),
            channel_id=channel_id,
            slack_run_id=slack_run_id,
        )
        await _prompts().put(thread_id, prompt)
        task = create_task(_wait_for_feedback(prompt))
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)


async def schedule_answer_feedback(thread_id: str, run_id: str, metadata: dict[str, Any]) -> None:
    try:
        from agent.slack.client import lookup_slack_run_message_mapping

        origin = SourceContext.from_metadata(metadata).slack_thread
        channel_id = origin.channel_id if origin else ""
        prompt = await _prompts().get(thread_id)
        eligible = prompt is not None and prompt.reason == "answer" and prompt.run_id == run_id
        if channel_id:
            mapping = await lookup_slack_run_message_mapping(langgraph_client(), channel_id, run_id)
            eligible = (
                bool(
                    mapping
                    and mapping.get("run_id") == run_id
                    and mapping.get("should_ask_for_feedback") is True
                )
                or eligible
            )
        if eligible:
            await _schedule(
                thread_id,
                run_id,
                metadata,
                reason="answer",
                event_id=f"answer:{run_id}",
                channel_id=channel_id,
                slack_run_id=run_id if channel_id else "",
            )
    except Exception:
        logger.warning(
            "Could not schedule answered-question feedback", extra={"thread_id": thread_id}
        )


async def schedule_pr_feedback(thread_id: str, metadata: dict[str, Any], pr_url: str) -> None:
    try:
        records = metadata.get("pull_requests")
        records = records if isinstance(records, list) else []
        record = next(
            (item for item in records if isinstance(item, dict) and item.get("url") == pr_url), {}
        )
        origin = record.get("slack_feedback")
        origin = origin if isinstance(origin, dict) else {}
        run_id = str(origin.get("run_id") or "")
        await _schedule(
            thread_id,
            run_id,
            metadata,
            reason="merged_pr",
            event_id=f"merged_pr:{pr_url}",
            channel_id=str(origin.get("channel_id") or ""),
            slack_run_id=run_id,
        )
    except Exception:
        logger.warning("Could not schedule merged-PR feedback", extra={"thread_id": thread_id})


async def feedback_prompt_status(
    thread_id: str, *, include_pending: bool = False
) -> tuple[str, int | None]:
    completed = await _completions().get(thread_id)
    if completed is not None:
        return completed.status, None
    prompt = await _prompts().get(thread_id)
    if prompt is None:
        return "unavailable", None
    client = langgraph_client()
    thread = await client.threads.get(thread_id)
    metadata = thread.get("metadata") or {}
    activity_at = _timestamp_ms(metadata.get(ACTIVITY_KEY))
    runs = await client.runs.list(thread_id, limit=1)
    latest = runs[0] if runs else {}
    if prompt.reason == "answer" and not latest:
        return "unavailable", None
    if prompt.reason == "answer" and (
        activity_at > prompt.activity_at_ms
        or (latest.get("run_id") and latest["run_id"] != prompt.run_id)
    ):
        return "unavailable", None
    due_at = max(prompt.due_at_ms, activity_at + FEEDBACK_DELAY_MS)
    if latest:
        if latest.get("status") in {"pending", "running"} or thread.get("status") == "busy":
            return "waiting", max(due_at, _now_ms() + FEEDBACK_DELAY_MS)
        if latest.get("status") != "success":
            return "unavailable", None
        due_at = max(due_at, _timestamp_ms(latest.get("updated_at")) + FEEDBACK_DELAY_MS)
    elif thread.get("status") == "busy":
        return "waiting", max(due_at, _now_ms() + FEEDBACK_DELAY_MS)
    # Only the live timer can activate a prompt; elapsed time cannot revive it after a restart.
    if not prompt.ready and not include_pending and _now_ms() >= due_at:
        return "unavailable", None
    return ("ready" if _now_ms() >= due_at else "waiting"), due_at


async def _wait_for_feedback(prompt: FeedbackPrompt) -> None:
    due_at = prompt.due_at_ms
    try:
        while True:
            await sleep(max(0, (due_at - _now_ms()) / 1000))
            async with agent_thread_pr_state_lock(langgraph_client(), prompt.thread_id):
                current = await _prompts().get(prompt.thread_id)
                if current is None or current.generation != prompt.generation:
                    return
                status, next_due = await feedback_prompt_status(
                    prompt.thread_id, include_pending=True
                )
                if status == "waiting" and next_due is not None:
                    due_at = next_due
                    continue
                if status != "ready":
                    return
                current.ready = True
                await _prompts().put(prompt.thread_id, current)
            if current.channel_id and current.slack_run_id:
                from agent.slack.thread_feedback import post_slack_feedback_prompt

                await post_slack_feedback_prompt(
                    current.thread_id,
                    current.slack_run_id,
                    current.channel_id,
                    expected_generation=current.generation,
                )
            return
    except Exception:
        logger.warning("Could not deliver feedback prompt", extra={"thread_id": prompt.thread_id})


async def feedback_generation_is_ready(thread_id: str, generation: str) -> bool:
    current = await _prompts().get(thread_id)
    return (
        current is not None
        and current.generation == generation
        and (await feedback_prompt_status(thread_id))[0] == "ready"
    )
