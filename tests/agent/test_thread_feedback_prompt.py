import json
from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from agent import thread_feedback as feedback
from agent.scheduler import get_scheduler
from agent.slack import thread_feedback as slack_feedback


@pytest.fixture
async def context(fake_store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    client = AsyncMock()
    client.now = 2000
    client.threads.get.return_value = {
        "thread_id": "t1",
        "status": "idle",
        "metadata": {feedback.ACTIVITY_KEY: 1000},
    }
    client.runs.list.return_value = [{"run_id": "r1", "status": "success"}]

    @asynccontextmanager
    async def unlocked(*args: Any, **kwargs: Any):
        yield

    monkeypatch.setattr(feedback, "langgraph_client", lambda: client)
    monkeypatch.setattr(feedback, "agent_thread_pr_state_lock", unlocked)
    monkeypatch.setattr(feedback, "now_ms", lambda: client.now)
    monkeypatch.setattr(slack_feedback, "post_slack_feedback_prompt", AsyncMock())
    return client


async def _schedule(
    client: Any, *, reason: Literal["answer", "merged_pr"] = "answer"
) -> dict[str, Any]:
    run_id = client.runs.list.return_value[0]["run_id"]
    await feedback._schedule(
        "t1",
        client.threads.get.return_value["metadata"],
        answer_run_id=run_id if reason == "answer" else "",
        event_id=f"{reason}:{run_id}",
        channel_id="C1",
        slack_run_id=run_id,
    )
    return client.runs.create.call_args.kwargs["input"]


async def _run(client: Any, payload: dict[str, Any], now: int) -> dict[str, Any]:
    client.now = now
    # The scheduler receives serialized state in a separate run/process.
    result = await get_scheduler().ainvoke(json.loads(json.dumps(payload)))
    return result["result"]


async def test_schedules_isolated_delayed_job_and_delivers_once(context: Any) -> None:
    payload = await _schedule(context)
    queued = context.runs.create.call_args
    assert queued.args == (None, "scheduler")
    assert queued.kwargs["after_seconds"] == 300
    assert queued.kwargs["on_completion"] == "delete"
    assert await feedback.feedback_prompt_status("t1") == "unavailable"
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    assert await _run(context, payload, 302000) == {"status": "ready"}
    assert await feedback.feedback_prompt_status("t1") == "ready"
    assert await _run(context, payload, 400000) == {"status": "skipped"}
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()


@pytest.mark.parametrize("change", ["activity", "new_run", "completed", "dismissed"])
async def test_new_activity_or_completion_suppresses_pending_prompt(
    context: Any, change: str
) -> None:
    payload = await _schedule(context)
    if change == "activity":
        context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 200000
    elif change == "new_run":
        context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    else:
        await feedback.complete_feedback_prompt("t1", change)
    assert await _run(context, payload, 400000) == {"status": "skipped"}
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()


async def test_new_qualifying_answer_supersedes_old_job(context: Any) -> None:
    old = await _schedule(context)
    context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 100000
    context.now = 200000
    new = await _schedule(context)
    assert await _run(context, old, 302000) == {"status": "skipped"}
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    await _run(context, new, 500000)
    assert slack_feedback.post_slack_feedback_prompt.await_args.args == ("t1", "r2", "C1")


async def test_merged_pr_waits_five_minutes_after_followup_finishes(context: Any) -> None:
    payload = await _schedule(context, reason="merged_pr")
    context.runs.list.return_value = [{"run_id": "r2", "status": "running"}]
    assert await _run(context, payload, 302000) == {"status": "deferred"}
    assert context.runs.create.call_args.kwargs["after_seconds"] == 300
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    context.runs.list.return_value = [
        {"run_id": "r2", "status": "success", "updated_at": "1970-01-01T00:06:00Z"}
    ]
    assert await _run(context, context.runs.create.call_args.kwargs["input"], 602000) == {
        "status": "deferred"
    }
    assert context.runs.create.call_args.kwargs["after_seconds"] == 58
    await _run(context, context.runs.create.call_args.kwargs["input"], 660000)
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()


async def test_duplicate_completion_does_not_schedule_or_send_twice(context: Any) -> None:
    payload = await _schedule(context)
    context.now = 100000
    await feedback._schedule(
        "t1", context.threads.get.return_value["metadata"], answer_run_id="r1", event_id="answer:r1"
    )
    assert context.runs.create.await_count == 1
    await _run(context, payload, 302000)
    assert await feedback.feedback_prompt_status("t1") == "ready"
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()


async def test_failed_delivery_is_not_retried(context: Any) -> None:
    slack_feedback.post_slack_feedback_prompt.side_effect = RuntimeError("Slack unavailable")
    payload = await _schedule(context)
    await _run(context, payload, 302000)
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()
    assert context.runs.create.await_count == 1


@pytest.mark.parametrize("status", ["error", "interrupted"])
async def test_unsuccessful_answers_do_not_prompt(context: Any, status: str) -> None:
    payload = await _schedule(context)
    context.runs.list.return_value = [{"run_id": "r1", "status": status}]
    await _run(context, payload, 302000)
    assert await feedback.feedback_prompt_status("t1") == "unavailable"
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()


async def test_late_completion_cannot_replace_newer_answer(context: Any) -> None:
    context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    await _schedule(context)
    await feedback._schedule(
        "t1", context.threads.get.return_value["metadata"], answer_run_id="r1", event_id="answer:r1"
    )
    prompt = await feedback.feedback_store().get("t1")
    assert prompt is not None and prompt.answer_run_id == "r2"


async def test_answering_queued_followup_in_same_run_restarts_quiet_period(context: Any) -> None:
    old = await _schedule(context)
    context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 100000
    context.now = 200000
    new = await _schedule(context)
    assert await _run(context, old, 302000) == {"status": "skipped"}
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    await _run(context, new, 500000)
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()


async def test_failed_enqueue_allows_later_completion_to_schedule(context: Any) -> None:
    context.runs.create.side_effect = [RuntimeError("LSD unavailable"), {}]
    await _schedule(context)
    assert await feedback.feedback_prompt_status("t1") == "unavailable"
    payload = await _schedule(context)
    assert context.runs.create.await_count == 2
    await _run(context, payload, 302000)
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()


async def test_web_pr_does_not_schedule_slack_feedback(context: Any) -> None:
    await feedback.schedule_pr_feedback("t1", {"pull_requests": [{"url": "pr1"}]}, "pr1")
    context.runs.create.assert_not_awaited()
