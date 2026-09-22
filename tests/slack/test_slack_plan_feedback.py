import json
from unittest.mock import AsyncMock, create_autospec
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, Request

from agent.slack import plan_feedback, routes
from agent.slack.payloads import SlackChannelContext
from agent.threads import plan_api, plan_store
from agent.utils.json_types import JsonObject


def _request(payload: JsonObject) -> Request:
    body = urlencode({"payload": json.dumps(payload)}).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/webhooks/slack/interactivity", "headers": []},
        receive,
    )


def _context(thread_ts: str = "1.0") -> plan_feedback.PlanFeedbackContext:
    return plan_feedback.PlanFeedbackContext(
        thread_id="thread-1",
        channel_id="C1",
        thread_ts=thread_ts,
        message_ts="2.0",
        user_id="U2",
        fingerprint=plan_store.plan_fingerprint({"html": "plan", "status": "ready"}),
    )


def _submission(
    context: plan_feedback.PlanFeedbackContext, feedback: str = "Add rollback"
) -> JsonObject:
    return {
        "type": "view_submission",
        "user": {"id": "U2", "name": "Reviewer"},
        "team": {"id": "T1"},
        "view": {
            "callback_id": plan_feedback.CALLBACK_ID,
            "private_metadata": context.model_dump_json(),
            "state": {
                "values": {
                    plan_feedback.FEEDBACK_BLOCK: {
                        plan_feedback.FEEDBACK_ACTION: {"value": feedback}
                    }
                }
            },
        },
    }


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    monkeypatch.setattr(routes.common, "verify_slack_signature", lambda **kwargs: True)
    mocks = {
        "open": AsyncMock(return_value=True),
        "notify": AsyncMock(return_value=True),
        "channel": AsyncMock(
            return_value=SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)
        ),
        "mapping": AsyncMock(return_value="thread-1"),
        "metadata": AsyncMock(
            return_value={
                "source": "slack",
                "plan_mode": True,
                "plan_status": "ready",
                "owner_login": "owner",
                "source_context": {
                    "slack_thread": {
                        "channel_id": "C1",
                        "thread_ts": "1.0",
                        "triggering_user_id": "U1",
                    }
                },
            }
        ),
        "content": AsyncMock(return_value={"html": "plan", "status": "ready"}),
        "comment": create_autospec(plan_store.add_plan_comment),
        "status": AsyncMock(),
        "dispatch": AsyncMock(return_value={"run_id": "run-1"}),
    }
    monkeypatch.setattr(plan_feedback, "open_slack_modal", mocks["open"])
    monkeypatch.setattr(plan_feedback, "post_slack_ephemeral_message", mocks["notify"])
    monkeypatch.setattr(plan_feedback.SlackChannel, "context_for", mocks["channel"])
    monkeypatch.setattr(plan_feedback, "lookup_slack_thread_id", mocks["mapping"])
    monkeypatch.setattr(
        plan_feedback,
        "get_slack_user_info",
        AsyncMock(return_value={"profile": {"email": "reviewer@example.com"}, "tz": "UTC"}),
    )
    monkeypatch.setattr(plan_feedback.User, "login_for_slack", AsyncMock(return_value="reviewer"))
    monkeypatch.setattr(plan_api, "fetch_thread_metadata", mocks["metadata"])
    monkeypatch.setattr(plan_api, "get_plan_content", mocks["content"])
    monkeypatch.setattr(plan_api, "add_plan_comment", mocks["comment"])
    monkeypatch.setattr(
        plan_api,
        "list_plan_comments",
        AsyncMock(return_value=[{"author": "Reviewer", "body": "Add rollback"}]),
    )
    monkeypatch.setattr(plan_api, "set_plan_status", mocks["status"])
    monkeypatch.setattr(plan_api, "dispatch_agent_run", mocks["dispatch"])
    return mocks


async def test_request_changes_opens_feedback_modal(
    setup: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    update = AsyncMock()
    monkeypatch.setattr(routes, "_update_selected_option_message", update)
    context = _context()
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(
        _request(
            {
                "type": "block_actions",
                "trigger_id": "trigger",
                "channel": {"id": "C1"},
                "user": {"id": "U2"},
                "message": {"ts": "2.0", "thread_ts": "1.0"},
                "actions": [
                    {
                        "action_id": "open_swe_option_select_1",
                        "value": json.dumps(
                            {
                                "type": "plan_approval",
                                "action": "revise",
                                "thread_id": context.thread_id,
                                "thread_ts": context.thread_ts,
                                "fingerprint": context.fingerprint,
                            }
                        ),
                    }
                ],
            }
        ),
        tasks,
    )
    assert result["status"] == "accepted"
    view = setup["open"].await_args.args[1]
    assert view["callback_id"] == plan_feedback.CALLBACK_ID
    assert view["blocks"][0]["element"]["multiline"] is True
    assert (
        plan_feedback.PlanFeedbackContext.model_validate_json(view["private_metadata"]) == context
    )
    assert tasks.tasks == []
    update.assert_not_awaited()


async def test_invalid_feedback_stays_in_modal(setup: dict[str, AsyncMock]) -> None:
    tasks = BackgroundTasks()
    result = await routes.slack_interactivity(_request(_submission(_context(), " ")), tasks)
    assert result["response_action"] == "errors"
    assert tasks.tasks == []


@pytest.mark.parametrize("thread_ts,reply_ts", [("1.0", ""), ("0", "5.0")])
async def test_submission_revises_as_reviewer(
    setup: dict[str, AsyncMock], thread_ts: str, reply_ts: str
) -> None:
    context = _context(thread_ts).model_copy(update={"reply_thread_ts": reply_ts})
    setup["metadata"].return_value["source_context"]["slack_thread"]["thread_ts"] = thread_ts
    tasks = BackgroundTasks()
    assert await routes.slack_interactivity(_request(_submission(context)), tasks) == {}
    setup["dispatch"].assert_not_awaited()
    await tasks()
    setup["comment"].assert_awaited_once_with(
        "thread-1", author="Reviewer", author_login="reviewer", body="Add rollback", anchor=None
    )
    config = setup["dispatch"].await_args.args[2]
    assert config["github_login"] == "reviewer"
    assert config["slack_thread"]["triggering_user_id"] == "U2"
    assert config["slack_thread"]["thread_ts"] == thread_ts
    assert config["slack_thread"]["reply_thread_ts"] == reply_ts


@pytest.mark.parametrize("failure", ["private", "external", "stale"])
async def test_unauthorized_or_stale_submission_is_rejected(
    setup: dict[str, AsyncMock], failure: str
) -> None:
    if failure == "private":
        setup["metadata"].return_value["visibility"] = "private"
    elif failure == "external":
        setup["channel"].return_value = SlackChannelContext(is_ext_shared=True)
    else:
        setup["content"].return_value["revision"] = "new-publication"
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_submission(_context())), tasks)
    await tasks()
    setup["comment"].assert_not_awaited()
    setup["dispatch"].assert_not_awaited()
    setup["notify"].assert_awaited_once()


async def test_dispatch_failure_restores_reviewable_plan_and_notifies(
    setup: dict[str, AsyncMock],
) -> None:
    setup["dispatch"].side_effect = RuntimeError("dispatch failed")
    tasks = BackgroundTasks()
    await routes.slack_interactivity(_request(_submission(_context())), tasks)
    await tasks()
    assert [call.args[1] for call in setup["status"].await_args_list] == ["revising", "ready"]
    assert "could not be started" in setup["notify"].await_args.args[2]


async def test_republishing_identical_plan_invalidates_old_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record: JsonObject = {}

    async def put(_namespace: list[str], _key: str, value: JsonObject) -> None:
        record.clear()
        record.update(value)

    monkeypatch.setattr(plan_store, "put_value", put)
    monkeypatch.setattr(plan_store, "get_value", AsyncMock(side_effect=lambda *_args: dict(record)))
    monkeypatch.setattr(plan_store, "_merge_thread_metadata", AsyncMock())
    await plan_store.save_plan_content("thread-1", html="same plan", clear_comments=False)
    original = plan_store.plan_fingerprint(record)
    await plan_store.save_plan_content("thread-1", html="same plan", clear_comments=False)
    assert plan_store.plan_fingerprint(record) != original


async def test_duplicate_submission_only_dispatches_once(setup: dict[str, AsyncMock]) -> None:
    async def status(_thread_id: str, status: str, *, plan_mode: bool) -> None:
        setup["metadata"].return_value["plan_status"] = status
        setup["content"].return_value["status"] = status

    setup["status"].side_effect = status
    for _ in range(2):
        tasks = BackgroundTasks()
        await routes.slack_interactivity(_request(_submission(_context())), tasks)
        await tasks()
    setup["dispatch"].assert_awaited_once()
    setup["comment"].assert_awaited_once()
