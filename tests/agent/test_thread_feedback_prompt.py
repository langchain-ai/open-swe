import asyncio
from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from agent import thread_feedback as feedback
from agent.slack import thread_feedback as slack_feedback


@pytest.fixture
async def context(fake_store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    client = AsyncMock()
    client.now = 2000
    client.waits = asyncio.Queue()
    client.threads.get.return_value = {
        "thread_id": "t1",
        "status": "idle",
        "metadata": {feedback.ACTIVITY_KEY: 1000},
    }
    client.runs.list.return_value = [{"run_id": "r1", "status": "success"}]

    @asynccontextmanager
    async def unlocked(*args: Any, **kwargs: Any):
        yield

    async def sleep(seconds: float) -> None:
        event = asyncio.Event()
        client.waits.put_nowait((seconds, event))
        await event.wait()

    async def wake(now: int) -> float:
        async with asyncio.timeout(1):
            seconds, event = await client.waits.get()
        client.now = now
        event.set()
        await asyncio.sleep(0)
        return seconds

    client.wake = wake
    monkeypatch.setattr(feedback, "langgraph_client", lambda: client)
    monkeypatch.setattr(feedback, "agent_thread_pr_state_lock", unlocked)
    monkeypatch.setattr(feedback, "now_ms", lambda: client.now)
    monkeypatch.setattr(feedback, "sleep", sleep)
    monkeypatch.setattr(slack_feedback, "post_slack_feedback_prompt", AsyncMock())
    yield client
    tasks = list(feedback._pending_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _schedule(
    client: Any, *, reason: Literal["answer", "merged_pr"] = "answer"
) -> asyncio.Task:
    previous = set(feedback._pending_tasks)
    run_id = client.runs.list.return_value[0]["run_id"]
    await feedback._schedule(
        "t1",
        client.threads.get.return_value["metadata"],
        answer_run_id=run_id if reason == "answer" else "",
        event_id=f"{reason}:{run_id}",
        channel_id="C1",
        slack_run_id=run_id,
    )
    task = (feedback._pending_tasks - previous).pop()
    await asyncio.sleep(0)
    return task


async def test_prompt_waits_five_minutes_without_creating_durable_run(context: Any) -> None:
    task = await _schedule(context)
    assert await feedback.feedback_prompt_status("t1") == "unavailable"
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    assert await context.wake(302000) == 300
    await task
    assert await feedback.feedback_prompt_status("t1") == "ready"
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()
    context.runs.create.assert_not_awaited()


async def test_restart_discards_pending_prompt_without_losing_submitted_feedback(
    context: Any,
) -> None:
    task = await _schedule(context)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    context.now = 400000
    assert await feedback.feedback_prompt_status("t1") == "unavailable"
    await feedback.complete_feedback_prompt("t2", "completed")
    assert await feedback.feedback_prompt_status("t2") == "completed"
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    context.runs.create.assert_not_awaited()


@pytest.mark.parametrize("change", ["activity", "new_run", "completed", "dismissed"])
async def test_new_activity_or_completion_suppresses_pending_prompt(
    context: Any, change: str
) -> None:
    task = await _schedule(context)
    if change == "activity":
        context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 200000
    elif change == "new_run":
        context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    else:
        await feedback.complete_feedback_prompt("t1", change)
    await context.wake(400000)
    await task
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()


async def test_new_qualifying_answer_replaces_old_timer(context: Any) -> None:
    old = await _schedule(context)
    context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 100000
    context.now = 200000
    new = await _schedule(context)
    await context.wake(302000)
    await old
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    await context.wake(500000)
    await new
    assert slack_feedback.post_slack_feedback_prompt.await_args.args == ("t1", "r2", "C1")


async def test_merged_pr_waits_five_minutes_after_followup_finishes(context: Any) -> None:
    task = await _schedule(context, reason="merged_pr")
    context.runs.list.return_value = [{"run_id": "r2", "status": "running"}]
    await context.wake(302000)
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    context.runs.list.return_value = [
        {"run_id": "r2", "status": "success", "updated_at": "1970-01-01T00:06:00Z"}
    ]
    await context.wake(602000)
    assert not task.done()
    await context.wake(660000)
    await task
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()


async def test_duplicate_completion_does_not_extend_timer_or_send_twice(context: Any) -> None:
    task = await _schedule(context)
    context.now = 100000
    await feedback._schedule(
        "t1", context.threads.get.return_value["metadata"], answer_run_id="r1", event_id="answer:r1"
    )
    await context.wake(302000)
    await task
    assert await feedback.feedback_prompt_status("t1") == "ready"
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()


async def test_failed_delivery_is_not_retried(context: Any) -> None:
    slack_feedback.post_slack_feedback_prompt.side_effect = RuntimeError("Slack unavailable")
    task = await _schedule(context)
    await context.wake(302000)
    await task
    assert context.waits.empty()
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()
    context.runs.create.assert_not_awaited()


@pytest.mark.parametrize("status", ["error", "interrupted"])
async def test_unsuccessful_answers_do_not_prompt(context: Any, status: str) -> None:
    task = await _schedule(context)
    context.runs.list.return_value = [{"run_id": "r1", "status": status}]
    await context.wake(302000)
    await task
    assert await feedback.feedback_prompt_status("t1") == "unavailable"
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()


async def test_web_answer_only_becomes_ready_after_run_succeeds(context: Any) -> None:
    await feedback.mark_answered_question("t1", "r1")
    task = next(iter(feedback._pending_tasks))
    context.runs.list.return_value = [{"run_id": "r1", "status": "running"}]
    await context.wake(302000)
    assert await feedback.feedback_prompt_status("t1") == "unavailable"
    context.runs.list.return_value = [
        {"run_id": "r1", "status": "success", "updated_at": "1970-01-01T00:06:00Z"}
    ]
    await context.wake(660000)
    await task
    assert await feedback.feedback_prompt_status("t1") == "ready"
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()


async def test_late_completion_cannot_replace_newer_answer(context: Any) -> None:
    context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    await feedback.mark_answered_question("t1", "r2")
    await feedback.mark_answered_question("t1", "r1")
    prompt = await feedback.feedback_store().get("t1")
    assert prompt is not None and prompt.answer_run_id == "r2"


async def test_answering_queued_followup_in_same_run_restarts_quiet_period(context: Any) -> None:
    old = await _schedule(context)
    context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 100000
    context.now = 200000
    new = await _schedule(context)
    await context.wake(302000)
    await old
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    await context.wake(500000)
    await new
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()
