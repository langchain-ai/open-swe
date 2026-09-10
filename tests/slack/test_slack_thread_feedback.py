import asyncio
import json
from typing import Any, Literal
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx2
import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from langgraph_sdk.errors import ConflictError

from agent import completion
from agent import thread_feedback as prompt_scheduler
from agent.slack import routes
from agent.slack import thread_feedback as feedback

_RESPONSE_URL = "https://hooks.slack.com/actions/T1/B1/test-response"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["other_user", "other_channel", "unknown_run", "external"])
async def test_feedback_interaction_requires_prompt_recipient(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    payload = _rating()
    if change == "other_user":
        payload["user"]["id"] = "U2"
    elif change == "other_channel":
        payload["channel"]["id"] = "C2"
    elif change == "unknown_run":
        payload = _rating(run_id="run-2")
    else:
        monkeypatch.setattr(
            feedback, "get_slack_channel_context", AsyncMock(return_value={"is_ext_shared": True})
        )
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(payload), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == context
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    feedback.respond_to_slack_interaction.assert_not_awaited()


def _request(payload: dict[str, Any]) -> Request:
    body = urlencode({"payload": json.dumps(payload)}).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


@pytest.fixture
def context(monkeypatch: pytest.MonkeyPatch, fake_store: Any) -> dict[str, Any]:
    record = {
        "agent_thread_id": "thread-1",
        "run_id": "run-1",
        "channel_id": "C1",
        "thread_ts": "1.0",
        "message_ts": "2.0",
        "user_id": "U1",
        "prompted": True,
    }
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", record)
    monkeypatch.setattr(routes.common, "verify_slack_signature", lambda **kwargs: True)
    monkeypatch.setattr(
        feedback,
        "get_slack_channel_context",
        AsyncMock(return_value={"is_ext_shared": False, "is_pending_ext_shared": False}),
    )
    monkeypatch.setattr(feedback, "post_slack_ephemeral_message", AsyncMock(return_value=True))
    monkeypatch.setattr(feedback, "respond_to_slack_interaction", AsyncMock(return_value=True))
    monkeypatch.setattr(feedback, "open_slack_modal", AsyncMock(return_value=True))
    monkeypatch.setattr(feedback, "create_langsmith_thread_feedback", AsyncMock(return_value=True))
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "source_context": {
                "slack_thread": {
                    "channel_id": "C1",
                    "thread_ts": "1.0",
                    "triggering_user_id": "U1",
                }
            }
        }
    }
    locks: set[str] = set()

    async def acquire(*, thread_id: str, **kwargs: Any) -> None:
        if thread_id in locks:
            response = httpx2.Response(409, request=httpx2.Request("POST", "http://test/threads"))
            raise ConflictError("already exists", response=response, body=None)
        locks.add(thread_id)

    async def release(thread_id: str) -> None:
        locks.remove(thread_id)

    client.threads.create.side_effect = acquire
    client.threads.delete.side_effect = release
    monkeypatch.setattr(feedback, "langgraph_client", lambda: client)
    return record


def _action(action_id: str, value: str = "run-1") -> dict[str, Any]:
    return {
        "type": "block_actions",
        "trigger_id": "trigger-1",
        "response_url": _RESPONSE_URL,
        "channel": {"id": "C1"},
        "user": {"id": "U1"},
        "actions": [{"action_id": action_id, "value": value, "action_ts": "3.0"}],
    }


def _rating(choice: str = "good", run_id: str = "run-1") -> dict[str, Any]:
    return _action("open_swe_feedback", json.dumps({"run_id": run_id, "choice": choice}))


async def _submit_rating(choice: str = "good") -> None:
    tasks = BackgroundTasks()
    assert await routes.slack_interactivity(_request(_rating(choice)), tasks) == {}
    await tasks()


def _submission(comment: str = "Please run the tests next time.") -> dict[str, Any]:
    return {
        "type": "view_submission",
        "user": {"id": "U1"},
        "view": {
            "callback_id": "open_swe_feedback_note",
            "private_metadata": json.dumps(
                {"channel_id": "C1", "run_id": "run-1", "response_url": _RESPONSE_URL}
            ),
            "state": {"values": {"feedback_comment": {"comment": {"value": comment}}}},
        },
    }


@pytest.mark.parametrize("valid", [False, True])
def test_feedback_http_response_preserves_modal_errors_and_empty_ack(
    context: Any, fake_store: Any, valid: bool
) -> None:
    fake_store.seed(
        ("slack_thread_feedback", "C1"),
        "run-1",
        {**context, "rating": 1, "choice": "bad", "completed": True},
    )
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/slack/interactivity",
            data={"payload": json.dumps(_submission("Helpful" if valid else "x" * 3001))},
        )
    assert response.status_code == 200
    if valid:
        assert response.json() == {}
    else:
        assert response.json()["response_action"] == "errors"
        assert response.json()["errors"]["feedback_comment"]


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", [None, "good"])
async def test_comment_requires_bad_rating(context: Any, choice: str | None) -> None:
    if choice:
        await _submit_rating(choice)
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_submission()), tasks)
    assert result["response_action"] == "errors"
    assert tasks.tasks == []


@pytest.mark.asyncio
async def test_empty_optional_comment_preserves_bad_rating(context: Any, fake_store: Any) -> None:
    await _submit_rating("bad")
    tasks = BackgroundTasks()
    assert await routes.slack_interactivity(_request(_submission("  ")), tasks) == {}
    await tasks()
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert saved["choice"] == "bad" and saved["completed"] and saved["comment"] == ""
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == 0.0
    feedback.post_slack_ephemeral_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_storage_failure_keeps_comment_modal_open(
    context: Any, monkeypatch: pytest.MonkeyPatch, fake_store: Any
) -> None:
    fake_store.seed(
        ("slack_thread_feedback", "C1"),
        "run-1",
        {**context, "rating": 1, "choice": "bad", "completed": True},
    )
    monkeypatch.setattr(fake_store, "put_item", AsyncMock(side_effect=RuntimeError("unavailable")))
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_submission()), tasks)
    assert result["response_action"] == "errors"
    assert "feedback_comment" in result["errors"]
    assert tasks.tasks == []
    feedback.respond_to_slack_interaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_uses_exact_run_mapping_and_deduplicates(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.values(("slack_thread_feedback", "C1")).clear()
    lookup = AsyncMock(
        return_value={
            "run_id": "run-1",
            "triggering_user_id": "U1",
            "message_ts": "2.0",
            "thread_ts": "1.0",
        }
    )
    monkeypatch.setattr(feedback, "lookup_slack_run_message_mapping", lookup)
    for _ in range(2):
        await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    feedback.post_slack_ephemeral_message.assert_awaited_once()
    call = feedback.post_slack_ephemeral_message.await_args
    blocks = call.kwargs["blocks"]
    assert not any(block["type"] == "input" for block in blocks)
    controls = next(block["elements"][0] for block in blocks if block["type"] == "context_actions")
    tasks = BackgroundTasks()
    await routes.slack_interactivity(
        _request(_action(controls["action_id"], controls["positive_button"]["value"])), tasks
    )
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["choice"] == "good"
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["prompted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("should_ask_for_feedback", [False, True])
async def test_success_completion_only_prompts_for_answered_question(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch, should_ask_for_feedback: bool
) -> None:
    fake_store.values(("slack_thread_feedback", "C1")).clear()
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "source": "slack",
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
        }
    }
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(prompt_scheduler, "langgraph_client", lambda: client)
    schedule = AsyncMock()
    monkeypatch.setattr(prompt_scheduler, "_schedule", schedule)
    monkeypatch.setattr(
        "agent.slack.client.lookup_slack_run_message_mapping",
        AsyncMock(
            return_value={
                "run_id": "run-1",
                "triggering_user_id": "U1",
                "message_ts": "2.0",
                "thread_ts": "1.0",
                "should_ask_for_feedback": should_ask_for_feedback,
            }
        ),
    )
    await completion.handle_run_completion(
        {"thread_id": "thread-1", "run_id": "run-1", "status": "success"}
    )
    assert schedule.await_count == int(should_ask_for_feedback)
    feedback.post_slack_ephemeral_message.assert_not_awaited()
    if should_ask_for_feedback:
        assert schedule.await_args.kwargs["answer_run_id"] == "run-1"
        assert schedule.await_args.kwargs["slack_run_id"] == "run-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["other_user", "other_channel", "unknown_run", "external", "bad_metadata", "long_comment"],
)
async def test_invalid_comment_keeps_modal_open(
    context: Any, fake_store: Any, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(
        ("slack_thread_feedback", "C1"),
        "run-1",
        {**context, "rating": 1, "choice": "bad", "completed": True, "comment": "Original"},
    )
    payload = _submission()
    if change == "other_user":
        payload["user"]["id"] = "U2"
    elif change == "other_channel":
        payload["view"]["private_metadata"] = json.dumps({"channel_id": "C2", "run_id": "run-1"})
    elif change == "unknown_run":
        payload["view"]["private_metadata"] = json.dumps({"channel_id": "C1", "run_id": "run-2"})
    elif change == "bad_metadata":
        payload["view"]["private_metadata"] = "{"
    elif change == "long_comment":
        payload = _submission("x" * 3001)
    else:
        monkeypatch.setattr(
            feedback, "get_slack_channel_context", AsyncMock(return_value={"is_ext_shared": True})
        )
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(payload), tasks)
    assert result["response_action"] == "errors"
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["comment"] == "Original"
    assert tasks.tasks == []


@pytest.mark.asyncio
@pytest.mark.parametrize("other_user", [False, True])
async def test_dismiss_removes_prompt_without_saving_feedback(
    context: Any, fake_store: Any, other_user: bool
) -> None:
    button = next(
        element
        for block in feedback.feedback_blocks("run-1", "thread-1")
        for element in block.get("elements", [])
        if element["action_id"] == "open_swe_feedback_dismiss"
    )
    payload = _action(button["action_id"], button["value"])
    if other_user:
        payload["user"]["id"] = "U2"
    tasks = BackgroundTasks()
    assert await routes.slack_interactivity(_request(payload), tasks) == {}
    await tasks()
    if other_user:
        feedback.respond_to_slack_interaction.assert_not_awaited()
    else:
        feedback.respond_to_slack_interaction.assert_awaited_once_with(
            _RESPONSE_URL, {"delete_original": True}
        )
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    feedback.post_slack_ephemeral_message.assert_not_awaited()
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert all(saved[key] == value for key, value in context.items())
    assert saved.get("dismissed", False) is not other_user
    assert saved.get("rating") is None
    assert not saved.get("comment")
    await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_url", [False, True])
async def test_prompt_cleanup_failure_preserves_saved_feedback_and_confirmation(
    context: Any, fake_store: Any, missing_url: bool
) -> None:
    payload = _rating()
    if missing_url:
        payload.pop("response_url")
    feedback.respond_to_slack_interaction.return_value = False
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(payload), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["rating"] == 5
    feedback.create_langsmith_thread_feedback.assert_awaited_once()
    if missing_url:
        feedback.post_slack_ephemeral_message.assert_not_awaited()
    else:
        feedback.post_slack_ephemeral_message.assert_awaited_once_with(
            "C1", "U1", "✅ Feedback completed. Thanks!", thread_ts="1.0"
        )


@pytest.mark.asyncio
async def test_dismiss_failure_keeps_feedback_submittable(context: Any, fake_store: Any) -> None:
    feedback.respond_to_slack_interaction.return_value = False
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action("open_swe_feedback_dismiss")), tasks)
    await tasks()
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert not saved.get("dismissed")
    feedback.post_slack_ephemeral_message.assert_not_awaited()
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    await _submit_rating()
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert saved["completed"] and saved["rating"] == 5


@pytest.mark.asyncio
async def test_dismiss_storage_failure_does_not_delete_prompt(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake_store, "put_item", AsyncMock(side_effect=RuntimeError("unavailable")))
    await feedback._dismiss_feedback(_action("open_swe_feedback_dismiss"))
    feedback.respond_to_slack_interaction.assert_not_awaited()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == context


@pytest.mark.asyncio
async def test_dismiss_retry_after_failed_rollback_cannot_save_late_feedback(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_put = fake_store.put_item
    writes = 0

    async def put(*args: Any, **kwargs: Any) -> Any:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("rollback unavailable")
        return await original_put(*args, **kwargs)

    monkeypatch.setattr(fake_store, "put_item", put)
    feedback.respond_to_slack_interaction.side_effect = [False, True]
    await feedback._dismiss_feedback(_action("open_swe_feedback_dismiss"))
    await _submit_rating()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["dismissed"]
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    await feedback._dismiss_feedback(_action("open_swe_feedback_dismiss"))
    assert feedback.respond_to_slack_interaction.await_count == 2
    assert feedback.respond_to_slack_interaction.await_args.args[1] == {"delete_original": True}


@pytest.mark.asyncio
async def test_dismiss_preserves_already_submitted_feedback(context: Any, fake_store: Any) -> None:
    await _submit_rating("bad")
    await routes.slack_interactivity(_request(_submission("Submitted")), BackgroundTasks())
    await feedback._dismiss_feedback(_action("open_swe_feedback_dismiss"))
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert (saved["rating"], saved["comment"], saved["completed"], saved["dismissed"]) == (
        1,
        "Submitted",
        True,
        True,
    )


@pytest.mark.asyncio
async def test_dismiss_during_failed_submission_update_prevents_later_acknowledgments(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def respond(url: str, message: dict[str, Any]) -> bool:
        if not started.is_set():
            started.set()
            await finish.wait()
            return False
        return True

    response_mock = AsyncMock(side_effect=respond)
    monkeypatch.setattr(feedback, "respond_to_slack_interaction", response_mock)
    async with asyncio.timeout(2):
        rating_task = asyncio.create_task(_submit_rating())
        await started.wait()
        dismiss_task = asyncio.create_task(
            feedback._dismiss_feedback(_action("open_swe_feedback_dismiss"))
        )
        await asyncio.sleep(0)
        finish.set()
        await asyncio.gather(rating_task, dismiss_task)
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["dismissed"] is True
    response_mock.reset_mock()
    feedback.post_slack_ephemeral_message.assert_awaited_once()
    feedback.post_slack_ephemeral_message.reset_mock()
    await feedback._acknowledge(
        feedback.ThreadFeedback(**context, rating=5, completed=True),
        response_url=_RESPONSE_URL,
    )
    response_mock.assert_not_awaited()
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_comment_during_dismissal_is_not_saved(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(
        ("slack_thread_feedback", "C1"),
        "run-1",
        {**context, "rating": 1, "choice": "bad", "completed": True},
    )
    started, finish = asyncio.Event(), asyncio.Event()

    async def respond(url: str, message: dict[str, Any]) -> bool:
        assert message == {"delete_original": True}
        started.set()
        await finish.wait()
        return True

    monkeypatch.setattr(feedback, "respond_to_slack_interaction", AsyncMock(side_effect=respond))
    async with asyncio.timeout(2):
        dismiss_task = asyncio.create_task(
            feedback._dismiss_feedback(_action("open_swe_feedback_dismiss"))
        )
        await started.wait()
        tasks = BackgroundTasks()
        result = await routes.slack_interactivity(_request(_submission("Helpful")), tasks)
        assert result["response_action"] == "errors"
        finish.set()
        await asyncio.gather(dismiss_task, tasks())
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert not saved["comment"]
    assert saved["dismissed"] is True
    feedback.create_langsmith_thread_feedback.assert_not_awaited()


@pytest.mark.asyncio
async def test_langsmith_failure_preserves_saved_feedback(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(feedback, "create_langsmith_thread_feedback", AsyncMock(return_value=False))
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_rating()), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["rating"] == 5


@pytest.mark.asyncio
async def test_failed_prompt_can_be_retried(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "prompted": False})
    post = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(feedback, "post_slack_ephemeral_message", post)
    await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    assert not fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["prompted"]
    await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["prompted"]


@pytest.mark.asyncio
async def test_prompt_exception_leaves_feedback_unsent(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "prompted": False})
    monkeypatch.setattr(
        feedback,
        "post_slack_ephemeral_message",
        AsyncMock(side_effect=RuntimeError("Slack unavailable")),
    )

    await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    assert not fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["prompted"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["activity", "completed", "dismissed", "event"])
async def test_scheduled_prompt_rechecks_readiness_after_channel_lookup(
    context: Any,
    fake_store: Any,
    monkeypatch: pytest.MonkeyPatch,
    change: Literal["activity", "completed", "dismissed", "event"],
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "prompted": False})
    prompt = {
        "event_id": "answer:run-1",
        "answer_run_id": "run-1",
        "activity_at_ms": 1000,
        "status": "ready",
    }
    fake_store.seed(("thread_feedback",), "thread-1", prompt)
    client = feedback.langgraph_client()
    client.threads.get.return_value["metadata"][prompt_scheduler.ACTIVITY_KEY] = 1000
    client.runs.list.return_value = [{"run_id": "run-1", "status": "success"}]
    monkeypatch.setattr(prompt_scheduler, "langgraph_client", lambda: client)
    monkeypatch.setattr(prompt_scheduler, "now_ms", lambda: 400000)
    assert await prompt_scheduler.feedback_event_is_ready("thread-1", "answer:run-1")

    async def channel_lookup(*args: Any, **kwargs: Any) -> dict[str, bool]:
        if change == "activity":
            client.threads.get.return_value["metadata"][prompt_scheduler.ACTIVITY_KEY] = 399000
        elif change == "event":
            fake_store.seed(
                ("thread_feedback",), "thread-1", {**prompt, "event_id": "answer:run-2"}
            )
        else:
            await prompt_scheduler.complete_feedback_prompt("thread-1", change)
        return {"is_ext_shared": False, "is_pending_ext_shared": False}

    monkeypatch.setattr(
        feedback, "get_slack_channel_context", AsyncMock(side_effect=channel_lookup)
    )

    await feedback.post_slack_feedback_prompt(
        "thread-1", "run-1", "C1", expected_event_id="answer:run-1"
    )
    feedback.post_slack_ephemeral_message.assert_not_awaited()
    assert not fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["prompted"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mapping",
    [
        None,
        {"run_id": "run-2"},
        {"run_id": "run-1", "message_ts": "2.0"},
        {"run_id": "run-1", "thread_ts": "1.0", "triggering_user_id": "U1"},
    ],
)
async def test_missing_exact_response_does_not_prompt(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch, mapping: Any
) -> None:
    fake_store.values(("slack_thread_feedback", "C1")).clear()
    monkeypatch.setattr(
        feedback, "lookup_slack_run_message_mapping", AsyncMock(return_value=mapping)
    )
    await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_feedback_requires_verified_slack_signature(
    context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes.common, "verify_slack_signature", lambda **kwargs: False)
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as exc:
        await routes.slack_interactivity(_request(_rating()), tasks)
    assert exc.value.status_code == 401
    assert tasks.tasks == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,kind", [("interrupted", None), ("error", None), ("success", "thread_wakeup")]
)
async def test_ineligible_completion_does_not_prompt(
    context: Any, monkeypatch: pytest.MonkeyPatch, status: str, kind: str | None
) -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "source": "slack",
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
        }
    }
    monkeypatch.setattr(completion, "langgraph_client", lambda: client)
    monkeypatch.setattr(completion, "post_slack_thread_reply", AsyncMock(return_value=True))
    prompt = AsyncMock()
    monkeypatch.setattr(completion, "schedule_answer_feedback", prompt)
    await completion.handle_run_completion(
        {"thread_id": "thread-1", "run_id": "run-1", "status": status, "metadata": {"kind": kind}}
    )
    prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("second_run", ["run-1", "run-2"])
@pytest.mark.parametrize("thread_ts", ["1.0", "0"])
async def test_concurrent_completion_callbacks_post_one_prompt(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch, second_run: str, thread_ts: str
) -> None:
    feedback.langgraph_client().threads.get.return_value["metadata"]["source_context"][
        "slack_thread"
    ]["thread_ts"] = thread_ts
    for run_id, message_ts in [("run-1", "2.0"), ("run-2", "3.0")]:
        fake_store.seed(
            ("slack_thread_feedback", "C1"),
            run_id,
            {
                **context,
                "run_id": run_id,
                "message_ts": message_ts,
                "thread_ts": thread_ts,
                "prompted": False,
            },
        )

    async def post(*args: Any, **kwargs: Any) -> bool:
        await asyncio.sleep(0.01)
        return True

    post_mock = AsyncMock(side_effect=post)
    monkeypatch.setattr(feedback, "post_slack_ephemeral_message", post_mock)
    await asyncio.gather(
        feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1"),
        feedback.post_slack_feedback_prompt("thread-1", second_run, "C1"),
    )
    post_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous_changes,current_changes,should_prompt",
    [
        ({}, {}, False),
        ({}, {"agent_thread_id": "thread-2"}, False),
        ({}, {"thread_ts": "4.0"}, True),
        ({}, {"user_id": "U2"}, False),
        ({}, {"channel_id": "C2"}, True),
        ({"thread_ts": "0"}, {"thread_ts": "0"}, False),
        ({"thread_ts": "0"}, {"thread_ts": "0", "agent_thread_id": "thread-2"}, True),
        ({"prompted": False}, {}, True),
    ],
)
async def test_prompt_deduplicates_existing_records_by_requester_and_slack_thread(
    context: Any,
    fake_store: Any,
    monkeypatch: pytest.MonkeyPatch,
    previous_changes: dict[str, Any],
    current_changes: dict[str, Any],
    should_prompt: bool,
) -> None:
    previous = {**context, "rating": 4, "comment": "Useful", **previous_changes}
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", previous)
    current = {**context, "run_id": "run-2", "message_ts": "3.0", **current_changes}
    origin = feedback.langgraph_client().threads.get.return_value["metadata"]["source_context"][
        "slack_thread"
    ]
    origin.update(channel_id=current["channel_id"], thread_ts=current["thread_ts"])
    monkeypatch.setattr(
        feedback,
        "lookup_slack_run_message_mapping",
        AsyncMock(
            return_value={
                "run_id": current["run_id"],
                "triggering_user_id": current["user_id"],
                "thread_ts": current["thread_ts"],
                "message_ts": current["message_ts"],
            }
        ),
    )

    await feedback.post_slack_feedback_prompt(
        current["agent_thread_id"], current["run_id"], current["channel_id"]
    )

    assert feedback.post_slack_ephemeral_message.await_count == int(should_prompt)
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == previous
    assert ("run-2" in fake_store.values(("slack_thread_feedback", current["channel_id"]))) == (
        should_prompt
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("merged_pr", [False, True])
async def test_followup_prompts_only_thread_initiator(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch, merged_pr: bool
) -> None:
    fake_store.values(("slack_thread_feedback", "C1")).clear()
    monkeypatch.setattr(
        feedback,
        "lookup_slack_run_message_mapping",
        AsyncMock(
            return_value={
                "run_id": "run-1",
                "triggering_user_id": "U2",
                "thread_ts": "1.0",
                "message_ts": "2.0",
                "should_ask_for_feedback": True,
            }
        ),
    )
    await feedback.post_slack_feedback_prompt(
        "thread-1", "run-1", "C1", require_answer=not merged_pr
    )

    feedback.post_slack_ephemeral_message.assert_awaited_once()
    assert feedback.post_slack_ephemeral_message.await_args.args[:2] == ("C1", "U1")
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert record["user_id"] == "U1"
    other_user_rating = _rating()
    other_user_rating["user"]["id"] = "U2"
    await routes.slack_interactivity(_request(other_user_rating), BackgroundTasks())
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["rating"] is None
    feedback.create_langsmith_thread_feedback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        None,
        {},
        {"channel_id": "C1", "thread_ts": "1.0"},
        {"channel_id": "C2", "thread_ts": "1.0", "triggering_user_id": "U1"},
        {"channel_id": "C1", "thread_ts": "9.0", "triggering_user_id": "U1"},
    ],
)
async def test_prompt_requires_known_initiator_at_same_slack_location(
    context: Any, fake_store: Any, origin: Any
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "prompted": False})
    feedback.langgraph_client().threads.get.return_value = {
        "metadata": {"source_context": {"slack_thread": origin}}
    }
    await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_unsent_prompt_for_other_user_is_not_reassigned(
    context: Any, fake_store: Any
) -> None:
    previous = {**context, "user_id": "U2", "prompted": False, "rating": 4}
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", previous)
    await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
    feedback.post_slack_ephemeral_message.assert_not_awaited()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == previous


@pytest.mark.asyncio
async def test_submission_during_prompt_delivery_is_preserved(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "prompted": False})
    started = asyncio.Event()
    finish_post = asyncio.Event()

    async def post(*args: Any, **kwargs: Any) -> bool:
        if kwargs.get("blocks"):
            started.set()
            await finish_post.wait()
        return True

    monkeypatch.setattr(feedback, "post_slack_ephemeral_message", AsyncMock(side_effect=post))
    async with asyncio.timeout(2):
        prompt_task = asyncio.create_task(
            feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
        )
        await started.wait()
        rating_task = asyncio.create_task(_submit_rating())
        finish_post.set()
        await asyncio.gather(prompt_task, rating_task)
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert record["prompted"] is True
    assert record["rating"] == 5
    assert record["acknowledged"] is True


def test_prompt_without_dashboard_has_no_broken_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback, "dashboard_thread_url", lambda _: None)
    text = feedback.feedback_blocks("run-1", "thread-1")[0]["text"]["text"]
    assert "<" not in text
    assert "this thread" in text


@pytest.mark.parametrize("choice,score", [("good", 1.0), ("bad", 0.0)])
@pytest.mark.parametrize("thread_ts", ["1.0", "0"])
async def test_native_rating_saves_immediately_and_only_bad_opens_comment(
    context, fake_store, choice, score, thread_ts
):
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "thread_ts": thread_ts})
    payload = _action("open_swe_feedback", json.dumps({"run_id": "run-1", "choice": choice}))
    tasks = BackgroundTasks()
    assert await routes.slack_interactivity(_request(payload), tasks) == {}
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert saved["completed"] and saved["choice"] == choice and saved["comment"] == ""
    if choice == "bad":
        view = feedback.open_slack_modal.await_args.args[1]
        assert view["callback_id"] == "open_swe_feedback_note"
        assert len(view["blocks"]) == 1
        assert view["blocks"][0]["optional"] is True
        assert view["blocks"][0]["element"]["type"] == "plain_text_input"
    else:
        feedback.open_slack_modal.assert_not_awaited()
    await tasks()
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == score
    assert fake_store.values(("thread_feedback",))["thread-1"]["status"] == "completed"
    feedback.respond_to_slack_interaction.assert_awaited_once_with(
        _RESPONSE_URL, {"delete_original": True}
    )
    feedback.post_slack_ephemeral_message.assert_awaited_once()
    confirmation = feedback.post_slack_ephemeral_message.await_args
    assert confirmation.args[:2] == ("C1", "U1")
    assert "completed" in confirmation.args[2].lower()
    assert confirmation.kwargs["thread_ts"] == (None if thread_ts == "0" else "1.0")
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["acknowledged"]


async def test_native_bad_comment_updates_rating_without_overwriting_first_comment(
    context, fake_store
):
    tasks = BackgroundTasks()
    rating = _action("open_swe_feedback", json.dumps({"run_id": "run-1", "choice": "bad"}))
    await routes.slack_interactivity(_request(rating), tasks)
    await tasks()
    feedback.post_slack_ephemeral_message.assert_awaited_once()
    feedback.respond_to_slack_interaction.reset_mock()
    payload = _submission("  The tests still fail.  ")
    payload["view"]["private_metadata"] = feedback.open_slack_modal.await_args.args[1][
        "private_metadata"
    ]
    tasks = BackgroundTasks()
    assert await routes.slack_interactivity(_request(payload), tasks) == {}
    await tasks()
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert saved["comment"] == "The tests still fail."
    assert saved["rating"] == 1 and saved["completed"]
    assert feedback.create_langsmith_thread_feedback.await_args.args == ("thread-1", "rating")
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == 0.0
    feedback.respond_to_slack_interaction.assert_awaited_once_with(
        _RESPONSE_URL, {"delete_original": True}
    )
    feedback.post_slack_ephemeral_message.assert_awaited_once()
    assert (
        feedback.create_langsmith_thread_feedback.await_args.kwargs["comment"]
        == "The tests still fail."
    )
    payload["view"]["state"]["values"]["feedback_comment"]["comment"]["value"] = "Overwrite"
    await routes.slack_interactivity(_request(payload), BackgroundTasks())
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == saved


@pytest.mark.parametrize("blocked", ["completed", "dismissed", "other_user"])
async def test_native_rating_cannot_reopen_finished_feedback_or_rate_for_someone_else(
    context, fake_store, blocked
):
    initial = {**context, **({blocked: True} if blocked != "other_user" else {})}
    if blocked == "completed":
        initial["acknowledged"] = True
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", initial)
    payload = _action("open_swe_feedback", json.dumps({"run_id": "run-1", "choice": "bad"}))
    if blocked == "other_user":
        payload["user"]["id"] = "U2"
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(payload), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == initial
    feedback.open_slack_modal.assert_not_awaited()


async def test_native_modal_failure_keeps_saved_bad_rating(context, fake_store):
    feedback.open_slack_modal.side_effect = RuntimeError("Slack unavailable")
    tasks = BackgroundTasks()
    payload = _action("open_swe_feedback", json.dumps({"run_id": "run-1", "choice": "bad"}))
    assert await routes.slack_interactivity(_request(payload), tasks) == {}
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["completed"]
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == 0.0


async def test_native_rating_retry_removes_controls_without_reopening_modal(context, fake_store):
    feedback.respond_to_slack_interaction.side_effect = [False, True]
    for choice in ("bad", "good"):
        tasks = BackgroundTasks()
        payload = _action("open_swe_feedback", json.dumps({"run_id": "run-1", "choice": choice}))
        await routes.slack_interactivity(_request(payload), tasks)
        await tasks()
    assert feedback.respond_to_slack_interaction.await_count == 2
    assert all(
        call.args == (_RESPONSE_URL, {"delete_original": True})
        for call in feedback.respond_to_slack_interaction.await_args_list
    )
    feedback.open_slack_modal.assert_awaited_once()
    feedback.post_slack_ephemeral_message.assert_awaited_once()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["choice"] == "bad"
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == 0.0


@pytest.mark.parametrize("failure", [False, TimeoutError()])
async def test_confirmation_failure_can_retry_without_repeating_success(
    context, fake_store, failure
):
    feedback.post_slack_ephemeral_message.side_effect = [failure, True]
    payload = _action("open_swe_feedback", json.dumps({"run_id": "run-1", "choice": "good"}))
    for deletions, expected_acknowledged in enumerate((False, True, True)):
        tasks = BackgroundTasks()
        await routes.slack_interactivity(_request(payload), tasks)
        await tasks()
        saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
        assert saved["completed"] and saved["rating"] == 5
        assert saved.get("acknowledged", False) is expected_acknowledged
        assert feedback.respond_to_slack_interaction.await_count == deletions
    assert feedback.post_slack_ephemeral_message.await_count == 2


@pytest.mark.parametrize(
    "slow_call", ["post_slack_ephemeral_message", "create_langsmith_thread_feedback"]
)
async def test_comment_saved_during_slow_confirmation_or_export_is_preserved(
    context: Any, fake_store: Any, slow_call: str
) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def wait_for_network(*args: Any, **kwargs: Any) -> bool:
        started.set()
        await finish.wait()
        return True

    getattr(feedback, slow_call).side_effect = wait_for_network
    async with asyncio.timeout(2):
        rating_task = asyncio.create_task(_submit_rating("bad"))
        await started.wait()
        comment_tasks = BackgroundTasks()
        assert (
            await routes.slack_interactivity(_request(_submission("Missing tests")), comment_tasks)
            == {}
        )
        assert (
            fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["comment"]
            == "Missing tests"
        )
        finish.set()
        await asyncio.gather(rating_task, comment_tasks())

    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert (
        saved["choice"] == "bad" and saved["comment"] == "Missing tests" and saved["acknowledged"]
    )
    exported = feedback.create_langsmith_thread_feedback.await_args.kwargs
    assert exported["score"] == 0.0 and exported["comment"] == "Missing tests"
    feedback.post_slack_ephemeral_message.assert_awaited_once()
