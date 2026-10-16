import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from unittest.mock import AsyncMock

import pytest

from openswe.slack import run_feedback
from openswe.slack.payloads import SlackChannelContext, SlackInteraction
from openswe.slack.records import SlackMessage


def _mapping(run_id: str, triggering_user_id: str | None) -> SlackMessage:
    return SlackMessage(
        channel_id="C1",
        message_ts="2.0",
        run_id=run_id,
        thread_ts="1.0",
        triggering_user_id=triggering_user_id,
    )


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
        AsyncMock(return_value=_mapping("run-1", "U1")),
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
        ("run-1", "slack_reply:C1:U1:2.0")
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
        _mapping("other-run", "U1"),
        _mapping("run-1", "other-user"),
        _mapping("run-1", None),
    ],
)
async def test_unknown_message_run_or_requester_cannot_be_rated(
    saved_feedback: AsyncMock, monkeypatch: pytest.MonkeyPatch, mapping: SlackMessage | None
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
