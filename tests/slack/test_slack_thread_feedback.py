import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx2
import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from langgraph_sdk.errors import ConflictError

from agent import completion
from agent.slack import client as slack_client
from agent.slack import routes
from agent.slack import thread_feedback as feedback


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
    monkeypatch.setattr(feedback, "open_slack_modal", AsyncMock(return_value=True))
    monkeypatch.setattr(feedback, "create_langsmith_thread_feedback", AsyncMock(return_value=True))
    client = AsyncMock()
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


def _action(action_id: str = "open_swe_feedback_rate_5", value: str = "run-1") -> dict[str, Any]:
    return {
        "type": "block_actions",
        "trigger_id": "trigger-1",
        "channel": {"id": "C1"},
        "user": {"id": "U1"},
        "actions": [{"action_id": action_id, "value": value, "action_ts": "3.0"}],
    }


@pytest.mark.asyncio
async def test_rating_is_saved_privately_without_starting_agent(
    context: Any, fake_store: Any
) -> None:
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_action()), tasks)
    assert result == {}
    assert len(tasks.tasks) == 1
    await tasks()

    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert record["rating"] == 5
    assert record["agent_thread_id"] == "thread-1"
    feedback.create_langsmith_thread_feedback.assert_awaited_once_with(
        "thread-1",
        "slack_rating:C1:U1:run-1",
        score=1.0,
        comment=None,
        source_info={
            "source": "slack_thread_feedback",
            "channel_id": "C1",
            "message_ts": "2.0",
            "user_id": "U1",
            "run_id": "run-1",
        },
    )
    call = feedback.post_slack_ephemeral_message.await_args
    assert call.args[:2] == ("C1", "U1")
    assert call.kwargs["thread_ts"] == "1.0"
    assert call.kwargs["blocks"][0]["accessory"]["action_id"] == "open_swe_feedback_comment"


@pytest.mark.asyncio
@pytest.mark.parametrize("rating,score", [(1, 0.0), (2, 0.25), (3, 0.5), (4, 0.75), (5, 1.0)])
async def test_rating_scale(context: Any, fake_store: Any, rating: int, score: float) -> None:
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action(f"open_swe_feedback_rate_{rating}")), tasks)
    await tasks()
    assert len(fake_store.values(("slack_thread_feedback", "C1"))) == 1
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == score


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["other_user", "other_channel", "unknown_run", "bad_rating", "external"]
)
async def test_invalid_rating_does_not_write(
    context: Any, fake_store: Any, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _action()
    if change == "other_user":
        payload["user"]["id"] = "U2"
    elif change == "other_channel":
        payload["channel"]["id"] = "C2"
    elif change == "unknown_run":
        payload["actions"][0]["value"] = "run-2"
    elif change == "bad_rating":
        payload["actions"][0]["action_id"] = "open_swe_feedback_rate_6"
    else:
        monkeypatch.setattr(
            feedback, "get_slack_channel_context", AsyncMock(return_value={"is_ext_shared": True})
        )
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(payload), tasks)
    await tasks()
    assert "rating" not in fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    feedback.create_langsmith_thread_feedback.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_modal_opens_with_saved_rating_and_comment(
    context: Any, fake_store: Any
) -> None:
    fake_store.seed(
        ("slack_thread_feedback", "C1"), "run-1", {**context, "rating": 4, "comment": "Useful"}
    )
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_action("open_swe_feedback_comment")), tasks)
    assert result == {}
    feedback.open_slack_modal.assert_awaited_once()
    trigger_id, view = feedback.open_slack_modal.await_args.args
    assert trigger_id == "trigger-1"
    assert json.loads(view["private_metadata"]) == {"channel_id": "C1", "run_id": "run-1"}
    assert view["blocks"][-1]["element"]["initial_value"] == "Useful"


@pytest.mark.asyncio
async def test_original_prompt_opens_text_form_without_a_rating(context: Any) -> None:
    blocks = feedback.rating_blocks("run-1", "thread-1")
    button = blocks[0]["accessory"]
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action(button["action_id"], button["value"])), tasks)
    feedback.open_slack_modal.assert_awaited_once()
    view = feedback.open_slack_modal.await_args.args[1]
    assert not view["blocks"][-1]["optional"]
    assert all(
        "Your rating:" not in block.get("text", {}).get("text", "") for block in view["blocks"]
    )


def _submission(comment: str = "Please run the tests next time.") -> dict[str, Any]:
    return {
        "type": "view_submission",
        "user": {"id": "U1"},
        "view": {
            "callback_id": "open_swe_feedback_comment",
            "private_metadata": json.dumps({"channel_id": "C1", "run_id": "run-1"}),
            "state": {"values": {"feedback_comment": {"comment": {"value": comment}}}},
        },
    }


@pytest.mark.parametrize("valid", [False, True])
def test_feedback_http_response_preserves_modal_errors_and_empty_ack(
    context: Any, valid: bool
) -> None:
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/slack/interactivity",
            data={"payload": json.dumps(_submission("Helpful" if valid else " "))},
        )
    assert response.status_code == 200
    if valid:
        assert response.json() == {}
    else:
        assert response.json()["response_action"] == "errors"
        assert response.json()["errors"]["feedback_comment"]


@pytest.mark.asyncio
async def test_comment_submission_updates_same_feedback(context: Any, fake_store: Any) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "rating": 2})
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_submission()), tasks)
    assert result == {}
    assert (
        fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["comment"]
        == "Please run the tests next time."
    )
    await tasks()
    assert feedback.create_langsmith_thread_feedback.await_args.args == (
        "thread-1",
        "slack_rating:C1:U1:run-1",
    )
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == 0.25


@pytest.mark.asyncio
async def test_comment_only_feedback_exports_without_inventing_rating(
    context: Any, fake_store: Any
) -> None:
    tasks = BackgroundTasks()
    assert (
        await routes.slack_interactivity(_request(_submission("  Helpful explanation  ")), tasks)
        == {}
    )
    await tasks()
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert record["comment"] == "Helpful explanation"
    assert record["rating"] is None
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] is None
    assert (
        feedback.create_langsmith_thread_feedback.await_args.kwargs["comment"]
        == "Helpful explanation"
    )

    rating_tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action()), rating_tasks)
    await rating_tasks()
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == 1.0
    assert (
        feedback.create_langsmith_thread_feedback.await_args.kwargs["comment"]
        == "Helpful explanation"
    )


@pytest.mark.asyncio
async def test_empty_comment_without_rating_keeps_form_open(context: Any) -> None:
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_submission("  ")), tasks)
    assert result["response_action"] == "errors"
    assert tasks.tasks == []


@pytest.mark.asyncio
async def test_storage_failure_keeps_comment_modal_open(
    context: Any, monkeypatch: pytest.MonkeyPatch, fake_store: Any
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "rating": 2})
    monkeypatch.setattr(fake_store, "put_item", AsyncMock(side_effect=RuntimeError("unavailable")))
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_submission()), tasks)
    assert result["response_action"] == "errors"
    assert "feedback_comment" in result["errors"]
    assert tasks.tasks == []


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
    buttons = call.kwargs["blocks"][-1]["elements"]
    assert len(buttons) == 5
    assert [b["value"] for b in buttons] == ["run-1"] * 5
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
    monkeypatch.setattr(
        feedback,
        "lookup_slack_run_message_mapping",
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
    assert feedback.post_slack_ephemeral_message.await_count == int(should_ask_for_feedback)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["other_user", "other_channel", "unknown_run", "external", "bad_metadata", "long_comment"],
)
async def test_invalid_comment_keeps_modal_open(
    context: Any, fake_store: Any, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(
        ("slack_thread_feedback", "C1"), "run-1", {**context, "rating": 3, "comment": "Original"}
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
async def test_rating_failure_does_not_acknowledge_success(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake_store, "put_item", AsyncMock(side_effect=RuntimeError("unavailable")))
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action()), tasks)
    await tasks()
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    assert "could not be saved" in feedback.post_slack_ephemeral_message.await_args.args[2]


@pytest.mark.asyncio
async def test_langsmith_failure_preserves_saved_feedback(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(feedback, "create_langsmith_thread_feedback", AsyncMock(return_value=False))
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action()), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["rating"] == 5


@pytest.mark.asyncio
async def test_modal_open_failure_offers_retry(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "rating": 5})
    monkeypatch.setattr(feedback, "open_slack_modal", AsyncMock(return_value=False))
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action("open_swe_feedback_comment")), tasks)
    await tasks()
    assert "could not be opened" in feedback.post_slack_ephemeral_message.await_args.args[2]


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
@pytest.mark.parametrize(
    "mapping",
    [
        None,
        {"run_id": "run-2"},
        {"run_id": "run-1", "thread_ts": "1.0", "message_ts": "2.0"},
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
        await routes.slack_interactivity(_request(_action()), tasks)
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
    monkeypatch.setattr(completion, "post_slack_feedback_prompt", prompt)
    await completion.handle_run_completion(
        {"thread_id": "thread-1", "run_id": "run-1", "status": status, "metadata": {"kind": kind}}
    )
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_older_rating_retry_does_not_revert_newer_feedback(
    context: Any, fake_store: Any
) -> None:
    for rating, timestamp in [(2, "3.0"), (5, "4.0"), (2, "3.0")]:
        payload = _action(f"open_swe_feedback_rate_{rating}")
        payload["actions"][0]["action_ts"] = timestamp
        tasks = BackgroundTasks()
        await routes.slack_interactivity(_request(payload), tasks)
        await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["rating"] == 5
    assert feedback.create_langsmith_thread_feedback.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_completion_callbacks_post_one_prompt(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "prompted": False})

    async def post(*args: Any, **kwargs: Any) -> bool:
        await asyncio.sleep(0.01)
        return True

    post_mock = AsyncMock(side_effect=post)
    monkeypatch.setattr(feedback, "post_slack_ephemeral_message", post_mock)
    await asyncio.gather(
        *[feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1") for _ in range(2)]
    )
    post_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_rating_during_prompt_delivery_is_preserved(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "prompted": False})
    started = asyncio.Event()
    finish_post = asyncio.Event()

    async def post(*args: Any, **kwargs: Any) -> bool:
        if kwargs["blocks"][-1]["type"] == "actions":
            started.set()
            await finish_post.wait()
        return True

    monkeypatch.setattr(feedback, "post_slack_ephemeral_message", AsyncMock(side_effect=post))
    async with asyncio.timeout(2):
        prompt_task = asyncio.create_task(
            feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1")
        )
        await started.wait()
        rating_task = asyncio.create_task(feedback._process_rating(_action()))
        finish_post.set()
        await asyncio.gather(prompt_task, rating_task)
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert record["prompted"] is True
    assert record["rating"] == 5


@pytest.mark.asyncio
async def test_slow_export_does_not_block_comment_save_or_revert_it(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", {**context, "rating": 3})
    stale_record = feedback.ThreadFeedback(**context, rating=3)
    started = asyncio.Event()
    finish_export = asyncio.Event()

    async def export(*args: Any, **kwargs: Any) -> bool:
        if not started.is_set():
            started.set()
            await finish_export.wait()
        return True

    export_mock = AsyncMock(side_effect=export)
    monkeypatch.setattr(feedback, "create_langsmith_thread_feedback", export_mock)
    async with asyncio.timeout(2):
        first = asyncio.create_task(feedback._export_feedback(stale_record))
        await started.wait()
        tasks = BackgroundTasks()
        result = await routes.slack_interactivity(_request(_submission("Latest comment")), tasks)
        assert result == {}
        finish_export.set()
        await asyncio.gather(first, tasks())
    assert export_mock.await_args.kwargs["comment"] == "Latest comment"


def test_prompt_without_dashboard_has_no_broken_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback, "dashboard_thread_url", lambda _: None)
    text = feedback.rating_blocks("run-1", "thread-1")[0]["text"]["text"]
    assert "<" not in text
    assert "this thread" in text


@pytest.mark.asyncio
async def test_timed_out_export_releases_lock_for_newer_comment(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_store.seed(
        ("slack_thread_feedback", "C1"), "run-1", {**context, "rating": 3, "comment": "Original"}
    )
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda delay: original_timeout(delay / 100))
    monkeypatch.setattr(
        slack_client,
        "_SLACK_THREAD_MUTATION_LOCK_TIMEOUT_SECONDS",
        slack_client._SLACK_THREAD_MUTATION_LOCK_TIMEOUT_SECONDS / 100,
    )
    monkeypatch.setattr(slack_client, "_SLACK_THREAD_MUTATION_LOCK_RETRY_SECONDS", 0.001)
    started = asyncio.Event()

    async def export(*args: Any, **kwargs: Any) -> bool:
        if not started.is_set():
            started.set()
            await asyncio.Event().wait()
        return True

    export_mock = AsyncMock(side_effect=export)
    monkeypatch.setattr(feedback, "create_langsmith_thread_feedback", export_mock)
    stale_record = feedback.ThreadFeedback(**context, rating=3, comment="Original")
    async with original_timeout(1):
        first = asyncio.create_task(feedback._export_feedback(stale_record))
        await started.wait()
        fake_store.seed(
            ("slack_thread_feedback", "C1"),
            "run-1",
            {**context, "rating": 3, "comment": "Latest comment"},
        )
        await asyncio.gather(first, feedback._export_feedback(stale_record))
    assert export_mock.await_count == 2
    assert export_mock.await_args.kwargs["comment"] == "Latest comment"
