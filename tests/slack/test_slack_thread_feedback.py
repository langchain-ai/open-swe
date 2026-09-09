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
from agent.slack import routes
from agent.slack import thread_feedback as feedback

_RESPONSE_URL = "https://hooks.slack.com/actions/T1/B1/test-response"


def _inline_submission(rating: int | None = 5, comment: str = "Helpful") -> dict[str, Any]:
    payload = _action("open_swe_feedback_submit")
    payload["state"] = {
        "values": {
            "feedback_rating": {
                "open_swe_feedback_rating": {
                    "selected_option": {"value": str(rating)} if rating is not None else None
                }
            },
            "feedback_comment": {"comment": {"value": comment}},
        }
    }
    return payload


@pytest.mark.asyncio
async def test_inline_rating_and_comment_submit_together_and_complete(
    context: Any, fake_store: Any
) -> None:
    blocks = feedback.rating_blocks("run-1", "thread-1")
    assert {block["element"]["type"] for block in blocks if block["type"] == "input"} == {
        "radio_buttons",
        "plain_text_input",
    }
    tasks = BackgroundTasks()
    assert (
        await routes.slack_interactivity(_request(_inline_submission(4, "  Very helpful  ")), tasks)
        == {}
    )
    await tasks()
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert (record["rating"], record["comment"], record["completed"]) == (4, "Very helpful", True)
    feedback.create_langsmith_thread_feedback.assert_awaited_once()
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == 0.75
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["comment"] == "Very helpful"
    message = feedback.respond_to_slack_interaction.await_args.args[1]
    assert message["replace_original"] is True
    assert "completed" in message["text"].lower()
    assert all(
        block["type"] not in {"input", "actions"} and "accessory" not in block
        for block in message["blocks"]
    )
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_submission_cannot_be_overwritten(context: Any, fake_store: Any) -> None:
    for rating, comment in [(5, "Great"), (1, "Stale submission")]:
        tasks = BackgroundTasks()
        await routes.slack_interactivity(_request(_inline_submission(rating, comment)), tasks)
        await tasks()
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert (record["rating"], record["comment"], record["completed"]) == (5, "Great", True)


@pytest.mark.asyncio
async def test_inline_save_failure_keeps_draft_in_original_message(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake_store, "put_item", AsyncMock(side_effect=RuntimeError("unavailable")))
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_inline_submission(3, "Keep this draft")), tasks)
    await tasks()
    assert not fake_store.values(("slack_thread_feedback", "C1"))["run-1"].get("completed")
    message = feedback.respond_to_slack_interaction.await_args.args[1]
    inputs = {
        block["block_id"]: block["element"]
        for block in message["blocks"]
        if block["type"] == "input"
    }
    assert inputs["feedback_rating"]["initial_option"]["value"] == "3"
    assert inputs["feedback_comment"]["initial_value"] == "Keep this draft"
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rating,comment,score",
    [
        (1, "", 0.0),
        (2, "", 0.25),
        (3, "", 0.5),
        (4, "", 0.75),
        (5, "", 1.0),
        (None, "Comment only", None),
    ],
)
async def test_inline_optional_fields_and_rating_scale(
    context: Any, fake_store: Any, rating: int | None, comment: str, score: float | None
) -> None:
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_inline_submission(rating, comment)), tasks)
    await tasks()
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert (record["rating"], record["comment"], record["completed"]) == (rating, comment, True)
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["score"] == score


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["other_user", "other_channel", "unknown_run", "external"])
async def test_inline_submission_requires_prompt_recipient(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    payload = _inline_submission()
    if change == "other_user":
        payload["user"]["id"] = "U2"
    elif change == "other_channel":
        payload["channel"]["id"] = "C2"
    elif change == "unknown_run":
        payload["actions"][0]["value"] = "run-2"
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rating,comment", [(None, "  "), (0, "Draft"), (6, "Draft"), (3, "x" * 3001)]
)
async def test_invalid_inline_submission_keeps_prompt_open(
    context: Any, fake_store: Any, rating: int | None, comment: str
) -> None:
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_inline_submission(rating, comment)), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == context
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    message = feedback.respond_to_slack_interaction.await_args.args[1]
    assert message["replace_original"] is True
    assert any(block["type"] == "input" for block in message["blocks"])
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_changing_rating_does_not_submit_feedback(context: Any, fake_store: Any) -> None:
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action("open_swe_feedback_rating")), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == context
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    feedback.respond_to_slack_interaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_rating_button_opens_combined_form_without_saving(
    context: Any, fake_store: Any
) -> None:
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action("open_swe_feedback_rate_4")), tasks)
    await tasks()
    view = feedback.open_slack_modal.await_args.args[1]
    assert view["blocks"][0]["element"]["initial_option"]["value"] == "4"
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == context
    feedback.create_langsmith_thread_feedback.assert_not_awaited()
    payload = _submission("Combined")
    payload["view"]["private_metadata"] = view["private_metadata"]
    payload["view"]["state"] = _inline_submission(4, "Combined")["state"]
    submit_tasks = BackgroundTasks()
    assert await routes.slack_interactivity(_request(payload), submit_tasks) == {}
    await submit_tasks()
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert (record["rating"], record["comment"], record["completed"]) == (4, "Combined", True)


@pytest.mark.asyncio
async def test_dismissed_feedback_cannot_be_submitted(context: Any, fake_store: Any) -> None:
    previous = {**context, "dismissed": True}
    fake_store.seed(("slack_thread_feedback", "C1"), "run-1", previous)
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_inline_submission()), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == previous
    feedback.respond_to_slack_interaction.assert_not_awaited()
    feedback.create_langsmith_thread_feedback.assert_not_awaited()


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


def _action(action_id: str = "open_swe_feedback_rate_5", value: str = "run-1") -> dict[str, Any]:
    return {
        "type": "block_actions",
        "trigger_id": "trigger-1",
        "response_url": _RESPONSE_URL,
        "channel": {"id": "C1"},
        "user": {"id": "U1"},
        "actions": [{"action_id": action_id, "value": value, "action_ts": "3.0"}],
    }


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
    assert json.loads(view["private_metadata"]) == {
        "channel_id": "C1",
        "run_id": "run-1",
        "response_url": _RESPONSE_URL,
    }
    assert view["blocks"][-1]["element"]["initial_value"] == "Useful"


def _submission(comment: str = "Please run the tests next time.") -> dict[str, Any]:
    return {
        "type": "view_submission",
        "user": {"id": "U1"},
        "view": {
            "callback_id": "open_swe_feedback_comment",
            "private_metadata": json.dumps(
                {"channel_id": "C1", "run_id": "run-1", "response_url": _RESPONSE_URL}
            ),
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
    inputs = [block for block in call.kwargs["blocks"] if block["type"] == "input"]
    assert {block["block_id"] for block in inputs} == {"feedback_rating", "feedback_comment"}
    submit = call.kwargs["blocks"][-1]["elements"][0]
    assert submit["value"] == "run-1"
    assert submit["action_id"] == "open_swe_feedback_submit"
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
async def test_comment_submission_replaces_the_prompt_that_opened_the_modal(context: Any) -> None:
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action("open_swe_feedback_comment")), tasks)
    view = feedback.open_slack_modal.await_args.args[1]
    payload = _submission("Helpful")
    payload["view"]["private_metadata"] = view["private_metadata"]
    await routes.slack_interactivity(_request(payload), tasks)
    await tasks()
    url, message = feedback.respond_to_slack_interaction.await_args.args
    assert url == _RESPONSE_URL
    assert message["replace_original"] is True
    assert all("accessory" not in block and "elements" not in block for block in message["blocks"])
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_user", [False, True])
async def test_dismiss_removes_prompt_without_saving_feedback(
    context: Any, fake_store: Any, other_user: bool
) -> None:
    button = next(
        element
        for block in feedback.rating_blocks("run-1", "thread-1")
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
async def test_message_update_failure_preserves_feedback_without_posting_another_prompt(
    context: Any, fake_store: Any, missing_url: bool
) -> None:
    payload = _inline_submission()
    if missing_url:
        payload.pop("response_url")
    feedback.respond_to_slack_interaction.return_value = False
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(payload), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["rating"] == 5
    feedback.create_langsmith_thread_feedback.assert_awaited_once()
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismiss_failure_preserves_prompt_and_reports_retry(
    context: Any, fake_store: Any
) -> None:
    feedback.respond_to_slack_interaction.return_value = False
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_action("open_swe_feedback_dismiss")), tasks)
    await tasks()
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"] == context
    feedback.post_slack_ephemeral_message.assert_not_awaited()
    feedback.create_langsmith_thread_feedback.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismiss_during_failed_submission_update_prevents_later_acknowledgments(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, finish = asyncio.Event(), asyncio.Event()

    async def respond(url: str, message: dict[str, Any]) -> bool:
        if message.get("replace_original"):
            started.set()
            await finish.wait()
            return False
        return True

    response_mock = AsyncMock(side_effect=respond)
    monkeypatch.setattr(feedback, "respond_to_slack_interaction", response_mock)
    async with asyncio.timeout(2):
        rating_task = asyncio.create_task(feedback._process_submission(_inline_submission()))
        await started.wait()
        dismiss_task = asyncio.create_task(
            feedback._dismiss_feedback(_action("open_swe_feedback_dismiss"))
        )
        await asyncio.sleep(0)
        finish.set()
        await asyncio.gather(rating_task, dismiss_task)
    assert fake_store.values(("slack_thread_feedback", "C1"))["run-1"]["dismissed"] is True
    response_mock.reset_mock()
    await feedback._acknowledge(
        feedback.ThreadFeedback(**context, rating=5, completed=True),
        response_url=_RESPONSE_URL,
    )
    response_mock.assert_not_awaited()
    feedback.post_slack_ephemeral_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_saved_during_dismissal_is_preserved(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        assert await routes.slack_interactivity(_request(_submission("Helpful")), tasks) == {}
        finish.set()
        await asyncio.gather(dismiss_task, tasks())
    saved = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert saved["comment"] == "Helpful"
    assert saved["dismissed"] is True
    assert feedback.create_langsmith_thread_feedback.await_args.kwargs["comment"] == "Helpful"


@pytest.mark.asyncio
async def test_langsmith_failure_preserves_saved_feedback(
    context: Any, fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(feedback, "create_langsmith_thread_feedback", AsyncMock(return_value=False))
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_inline_submission()), tasks)
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
    if merged_pr:
        await feedback.post_slack_pr_feedback_prompt(
            "thread-1",
            {
                "pull_requests": [
                    {"url": "pr-url", "slack_feedback": {"run_id": "run-1", "channel_id": "C1"}}
                ]
            },
            "pr-url",
        )
    else:
        await feedback.post_slack_feedback_prompt("thread-1", "run-1", "C1", require_answer=True)

    feedback.post_slack_ephemeral_message.assert_awaited_once()
    assert feedback.post_slack_ephemeral_message.await_args.args[:2] == ("C1", "U1")
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert record["user_id"] == "U1"
    other_user_rating = _action()
    other_user_rating["user"]["id"] = "U2"
    await feedback._process_submission({**_inline_submission(), "user": {"id": "U2"}})
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
        rating_task = asyncio.create_task(feedback._process_submission(_inline_submission()))
        finish_post.set()
        await asyncio.gather(prompt_task, rating_task)
    record = fake_store.values(("slack_thread_feedback", "C1"))["run-1"]
    assert record["prompted"] is True
    assert record["rating"] == 5


def test_prompt_without_dashboard_has_no_broken_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback, "dashboard_thread_url", lambda _: None)
    text = feedback.rating_blocks("run-1", "thread-1")[0]["text"]["text"]
    assert "<" not in text
    assert "this thread" in text
