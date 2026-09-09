from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from agent import scheduler
from agent import thread_feedback as feedback
from agent.slack import thread_feedback as slack_feedback


@pytest.fixture
def context(fake_store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    client = AsyncMock()
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
    monkeypatch.setattr(feedback, "_now_ms", lambda: 2000)
    monkeypatch.setattr(slack_feedback, "post_slack_feedback_prompt", AsyncMock())
    return client


async def _schedule(
    client: Any, *, reason: Literal["answer", "merged_pr"] = "answer", event_id: str = "answer:r1"
) -> Any:
    await feedback._schedule(
        "t1",
        "r1",
        client.threads.get.return_value["metadata"],
        reason=reason,
        event_id=event_id,
        channel_id="C1",
        slack_run_id="r1",
    )
    return client.runs.create.await_args.kwargs["input"]


@pytest.mark.asyncio
async def test_prompt_waits_five_minutes_and_scheduler_dispatches(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _schedule(context)
    assert context.runs.create.await_args.kwargs["after_seconds"] == 300
    assert await feedback.feedback_prompt_status("t1") == ("waiting", 302000)
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    monkeypatch.setattr(feedback, "_now_ms", lambda: 302000)
    await scheduler._launch(scheduler.SchedulerState(**state), {})
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once_with(
        "t1", "r1", "C1", expected_generation=state["feedback_generation"]
    )


@pytest.mark.asyncio
async def test_new_user_message_suppresses_old_answer_prompt(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _schedule(context)
    context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 200000
    monkeypatch.setattr(feedback, "_now_ms", lambda: 400000)
    assert await feedback.run_feedback_prompt(state) == {"status": "unavailable"}
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_qualifying_answer_replaces_older_timer(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = await _schedule(context)
    context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 100000
    monkeypatch.setattr(feedback, "_now_ms", lambda: 200000)
    await feedback._schedule(
        "t1",
        "r2",
        context.threads.get.return_value["metadata"],
        reason="answer",
        event_id="answer:r2",
        channel_id="C1",
        slack_run_id="r2",
    )
    assert await feedback.run_feedback_prompt(old) == {"status": "superseded"}
    assert await feedback.feedback_prompt_status("t1") == ("waiting", 500000)


@pytest.mark.asyncio
async def test_merged_pr_waits_for_followup_run_to_finish(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _schedule(context, reason="merged_pr", event_id="pr:7")
    context.runs.list.return_value = [{"run_id": "r2", "status": "running"}]
    monkeypatch.setattr(feedback, "_now_ms", lambda: 302000)
    assert await feedback.run_feedback_prompt(state) == {"status": "postponed"}
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    context.runs.list.return_value = [
        {"run_id": "r2", "status": "success", "updated_at": "1970-01-01T00:06:00Z"}
    ]
    assert await feedback.feedback_prompt_status("t1") == ("waiting", 660000)
    monkeypatch.setattr(feedback, "_now_ms", lambda: 660000)
    assert await feedback.run_feedback_prompt(state) == {"status": "ready"}


@pytest.mark.asyncio
async def test_duplicate_completion_does_not_extend_timer(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _schedule(context)
    monkeypatch.setattr(feedback, "_now_ms", lambda: 100000)
    await _schedule(context)
    context.runs.create.assert_awaited_once()
    assert await feedback.feedback_prompt_status("t1") == ("waiting", 302000)


@pytest.mark.asyncio
async def test_failed_delivery_schedules_retry_and_stops_after_success(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await _schedule(context)
    context.runs.create.reset_mock()
    slack_feedback.post_slack_feedback_prompt.side_effect = [False, True]
    monkeypatch.setattr(feedback, "_now_ms", lambda: 302000)

    assert await feedback.run_feedback_prompt(state) == {"status": "retrying"}
    retry = context.runs.create.await_args.kwargs
    assert retry["after_seconds"] == 60
    assert retry["input"] == state

    monkeypatch.setattr(feedback, "_now_ms", lambda: 362000)
    assert await feedback.run_feedback_prompt(retry["input"]) == {"status": "ready"}
    assert slack_feedback.post_slack_feedback_prompt.await_count == 2
    context.runs.create.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["activity", "completed", "dismissed"])
async def test_delivery_retry_rechecks_activity_and_terminal_feedback(
    context: Any,
    monkeypatch: pytest.MonkeyPatch,
    change: Literal["activity", "completed", "dismissed"],
) -> None:
    state = await _schedule(context)
    slack_feedback.post_slack_feedback_prompt.return_value = False
    monkeypatch.setattr(feedback, "_now_ms", lambda: 302000)
    assert await feedback.run_feedback_prompt(state) == {"status": "retrying"}
    retry = context.runs.create.await_args.kwargs["input"]
    context.runs.create.reset_mock()
    if change == "activity":
        context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 310000
        expected = "unavailable"
    else:
        await feedback.complete_feedback_prompt("t1", change)
        expected = change
    monkeypatch.setattr(feedback, "_now_ms", lambda: 362000)

    assert await feedback.run_feedback_prompt(retry) == {"status": expected}
    slack_feedback.post_slack_feedback_prompt.assert_awaited_once()
    context.runs.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "error", "interrupted"])
async def test_question_marker_does_not_qualify_unsuccessful_runs(
    context: Any, status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await feedback.mark_answered_question("t1", "r1")
    context.runs.list.return_value = [{"run_id": "r1", "status": status}]
    monkeypatch.setattr(feedback, "_now_ms", lambda: 400000)
    assert (await feedback.feedback_prompt_status("t1"))[0] != "ready"


@pytest.mark.asyncio
async def test_web_answer_marker_qualifies_only_after_success_and_quiet_period(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await feedback.mark_answered_question("t1", "r1")
    context.runs.list.return_value = [
        {"run_id": "r1", "status": "success", "updated_at": "1970-01-01T00:01:00Z"}
    ]
    assert await feedback.feedback_prompt_status("t1") == ("waiting", 360000)
    monkeypatch.setattr(feedback, "_now_ms", lambda: 360000)
    assert await feedback.feedback_prompt_status("t1") == ("ready", 360000)


@pytest.mark.asyncio
async def test_new_run_without_activity_marker_invalidates_old_answer(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _schedule(context)
    context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    monkeypatch.setattr(feedback, "_now_ms", lambda: 400000)
    assert await feedback.feedback_prompt_status("t1") == ("unavailable", None)


@pytest.mark.asyncio
async def test_late_completion_cannot_replace_newer_answer(context: Any) -> None:
    context.runs.list.return_value = [{"run_id": "r2", "status": "success"}]
    await feedback.mark_answered_question("t1", "r2")
    await feedback.mark_answered_question("t1", "r1")
    prompt = await feedback._prompts().get("t1")
    assert prompt is not None and prompt.run_id == "r2"
    assert (await feedback.feedback_prompt_status("t1"))[0] == "waiting"


@pytest.mark.asyncio
async def test_answering_queued_followup_in_same_run_restarts_quiet_period(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await feedback.mark_answered_question("t1", "r1")
    context.threads.get.return_value["metadata"][feedback.ACTIVITY_KEY] = 100000
    monkeypatch.setattr(feedback, "_now_ms", lambda: 200000)
    assert await feedback.feedback_prompt_status("t1") == ("unavailable", None)
    await feedback.mark_answered_question("t1", "r1")
    assert await feedback.feedback_prompt_status("t1") == ("waiting", 500000)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "dismissed"])
async def test_completed_feedback_suppresses_other_surface_prompt(
    context: Any, monkeypatch: pytest.MonkeyPatch, status: Literal["completed", "dismissed"]
) -> None:
    state = await _schedule(context)
    await feedback.complete_feedback_prompt("t1", status)
    monkeypatch.setattr(feedback, "_now_ms", lambda: 400000)
    assert await feedback.run_feedback_prompt(state) == {"status": status}
    slack_feedback.post_slack_feedback_prompt.assert_not_awaited()
    assert await feedback.feedback_prompt_status("t1") == (status, None)


@pytest.mark.asyncio
async def test_marker_can_gain_slack_delivery_without_extending_delay(context: Any) -> None:
    await feedback.mark_answered_question("t1", "r1")
    state = await _schedule(context)
    assert state["agent_thread_id"] == "t1"
    prompt = await feedback._prompts().get("t1")
    assert prompt is not None and prompt.channel_id == "C1"
    assert prompt.due_at_ms == 302000


@pytest.mark.asyncio
async def test_merged_pr_uses_original_slack_request_and_qualifies_web_threads(
    context: Any,
) -> None:
    await feedback.schedule_pr_feedback(
        "t1",
        {
            "pull_requests": [
                {
                    "url": "pr:7",
                    "slack_feedback": {"channel_id": "C-original", "run_id": "r-original"},
                }
            ],
            "source_context": {"slack_thread": {"channel_id": "C-new"}},
        },
        "pr:7",
    )
    prompt = await feedback._prompts().get("t1")
    assert prompt is not None and prompt.channel_id == "C-original"
    assert prompt.slack_run_id == "r-original"
    context.runs.create.reset_mock()
    await feedback.schedule_pr_feedback("t2", {}, "pr:8")
    assert (await feedback.feedback_prompt_status("t2"))[0] == "waiting"
    context.runs.create.assert_not_awaited()
