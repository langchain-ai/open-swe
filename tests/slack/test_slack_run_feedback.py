import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, HTTPException, Request

from agent.slack import routes, run_feedback
from agent.slack.payloads import SlackBlockAction, SlackChannelContext, SlackInteraction
from agent.utils.json_types import JsonObject


def interaction(rating: Literal["up", "down"] = "up") -> SlackInteraction:
    return SlackInteraction.model_validate(
        {
            "type": "block_actions",
            "channel": {"id": "C1"},
            "user": {"id": "U1"},
            "message": {"ts": "2.0", "thread_ts": "1.0"},
            "actions": [
                {
                    "action_id": run_feedback.FEEDBACK_ACTION,
                    "type": "feedback_buttons",
                    "value": json.dumps({"run_id": "run-1", "rating": rating}),
                }
            ],
        }
    )


@pytest.fixture
async def saved_feedback(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    @asynccontextmanager
    async def unlocked(*args: object, **kwargs: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(run_feedback, "slack_thread_mutation_lock", unlocked)
    monkeypatch.setattr(
        run_feedback,
        "lookup_slack_run_mapping",
        AsyncMock(return_value={"run_id": "run-1", "triggering_user_id": "U1"}),
    )
    monkeypatch.setattr(
        run_feedback.SlackChannel,
        "context_for",
        AsyncMock(
            return_value=SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)
        ),
    )
    monkeypatch.setattr(run_feedback, "record_feedback_submission", AsyncMock())
    save = AsyncMock(return_value=True)
    monkeypatch.setattr(run_feedback, "create_langsmith_feedback", save)
    return save


async def test_rerating_updates_same_feedback_on_exact_reply_run(saved_feedback: AsyncMock) -> None:
    for rating in ("up", "down", "down"):
        payload = interaction(rating)
        await run_feedback.process_feedback(payload, payload.actions[0])

    assert [call.args for call in saved_feedback.await_args_list] == [
        ("run-1", "slack_reply_rating")
    ] * 3
    assert [call.kwargs["dedupe_key"] for call in saved_feedback.await_args_list] == [
        "slack_reply:C1:U1:2.0"
    ] * 3
    assert [call.kwargs["score"] for call in saved_feedback.await_args_list] == [1.0, 0.0, 0.0]
    assert saved_feedback.await_args is not None
    assert saved_feedback.await_args.kwargs["source_info"] == {
        "source": "slack_reply",
        "channel_id": "C1",
        "message_ts": "2.0",
        "user_id": "U1",
    }


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        {"run_id": "other-run", "triggering_user_id": "U1"},
        {"run_id": "run-1", "triggering_user_id": "other-user"},
        {"run_id": "run-1"},
    ],
)
async def test_unknown_message_run_or_requester_cannot_be_rated(
    saved_feedback: AsyncMock, monkeypatch: pytest.MonkeyPatch, mapping: JsonObject | None
) -> None:
    monkeypatch.setattr(run_feedback, "lookup_slack_run_mapping", AsyncMock(return_value=mapping))
    payload = interaction()
    await run_feedback.process_feedback(payload, payload.actions[0])
    saved_feedback.assert_not_awaited()


@pytest.mark.parametrize("shared", [True, None])
async def test_external_or_unknown_channel_cannot_be_rated(
    saved_feedback: AsyncMock, monkeypatch: pytest.MonkeyPatch, shared: bool | None
) -> None:
    monkeypatch.setattr(
        run_feedback.SlackChannel,
        "context_for",
        AsyncMock(return_value=SlackChannelContext(is_ext_shared=shared)),
    )
    payload = interaction()
    await run_feedback.process_feedback(payload, payload.actions[0])
    saved_feedback.assert_not_awaited()


@pytest.mark.parametrize("value", ["not json", "[]", "{}", '{"run_id":"run-1","rating":"bad"}'])
async def test_malformed_selection_is_ignored(saved_feedback: AsyncMock, value: str) -> None:
    payload = interaction()
    await run_feedback.process_feedback(payload, SlackBlockAction(value=value))
    saved_feedback.assert_not_awaited()


async def test_failed_export_is_logged_without_recording_submission(
    saved_feedback: AsyncMock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    saved_feedback.return_value = False
    analytics = AsyncMock()
    monkeypatch.setattr(run_feedback, "record_feedback_submission", analytics)
    payload = interaction()
    await run_feedback.process_feedback(payload, payload.actions[0])
    analytics.assert_not_awaited()
    assert "Could not save Slack reply feedback" in caplog.text


@pytest.mark.parametrize("signed", [True, False])
async def test_webhook_acknowledges_feedback_without_starting_agent(
    monkeypatch: pytest.MonkeyPatch, signed: bool
) -> None:
    body = urlencode({"payload": interaction().model_dump_json()}).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body}

    request = Request({"type": "http", "headers": []}, receive)
    tasks = BackgroundTasks()
    process = AsyncMock()
    monkeypatch.setattr(routes.common, "verify_slack_signature", lambda **kwargs: signed)
    monkeypatch.setattr(routes, "process_feedback", process)
    if not signed:
        with pytest.raises(HTTPException) as exc:
            await routes.slack_interactivity(request, tasks)
        assert exc.value.status_code == 401
        assert not tasks.tasks
        return

    assert await routes.slack_interactivity(request, tasks) == {}
    process.assert_not_awaited()
    await tasks()
    process.assert_awaited_once_with(interaction(), interaction().actions[0])
