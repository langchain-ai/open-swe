import json
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from agent.slack import forms
from agent.slack.payloads import SlackChannelContext, SlackViewSubmission


def _record() -> forms.FormRecord:
    return forms.FormRecord(
        id="f1",
        title="Review",
        items=[forms.FormItem(label="First", comment=True), forms.FormItem(label="Second")],
        channel_id="C1",
        thread_ts="1.0",
        thread_id="thread-1",
        user_id="U1",
    )


def _submission(user: str = "U1", selection: str = "0") -> SlackViewSubmission:
    return SlackViewSubmission.model_validate(
        {
            "type": "view_submission",
            "trigger_id": "3.0",
            "user": {"id": user},
            "view": {
                "id": "V1",
                "callback_id": forms.CALLBACK_ID,
                "private_metadata": json.dumps({"channel_id": "C1", "form_id": "f1"}),
                "state": {
                    "values": {
                        "choices": {"selected": {"selected_options": [{"value": selection}]}},
                        "comment_0": {"comment": {"value": "Details"}},
                    }
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_submission_ack_and_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _record()
    monkeypatch.setattr(
        forms,
        "_store",
        lambda _channel: type("Store", (), {"get": AsyncMock(return_value=record)})(),
    )
    monkeypatch.setattr(
        forms.common,
        "resolve_slack_channel_context",
        AsyncMock(
            return_value=SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)
        ),
    )
    monkeypatch.setattr(forms.common, "lookup_slack_thread_id", AsyncMock(return_value="thread-1"))
    monkeypatch.setattr(forms.common, "get_slack_repo_config", AsyncMock(return_value={}))
    process = AsyncMock()
    monkeypatch.setattr(forms.webhook, "process_slack_mention", process)
    tasks = BackgroundTasks()
    assert await forms.handle_submission(_submission(), tasks) == {}
    process.assert_not_awaited()
    await tasks()
    request = process.await_args.args[0]
    assert request.user_id == "U1"
    assert request.thread_id == "thread-1"
    assert "First\n  Comment: Details" in request.text


@pytest.mark.asyncio
async def test_rejects_wrong_user_and_invalid_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        forms,
        "_store",
        lambda _channel: type("Store", (), {"get": AsyncMock(return_value=_record())})(),
    )
    context = SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)
    monkeypatch.setattr(
        forms.common, "resolve_slack_channel_context", AsyncMock(return_value=context)
    )
    monkeypatch.setattr(forms.common, "lookup_slack_thread_id", AsyncMock(return_value="thread-1"))
    process = AsyncMock()
    monkeypatch.setattr(forms.webhook, "process_slack_mention", process)
    await forms._dispatch(_submission(user="U2"))
    await forms._dispatch(_submission(selection="999"))
    process.assert_not_awaited()
