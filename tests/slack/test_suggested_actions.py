import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from openswe.human_review import request_modal
from openswe.human_review.offer import GET_REVIEWED
from openswe.human_review.standard import RequestResult
from openswe.slack import events as slack_events
from openswe.slack import suggested_actions
from openswe.slack.payloads import SlackButtonValue, SlackChannelContext, SlackInteraction
from openswe.slack.request import SlackRequest
from openswe.users import User, UserIdentity
from openswe.webhooks import common


class _Tasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


async def test_accepting_a_suggestion_asks_the_slack_threads_agent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slack_events.reset_slack_event_claims()
    monkeypatch.setattr(slack_events, "_claim_remotely", AsyncMock(return_value=True))
    respond = AsyncMock(return_value=True)
    monkeypatch.setattr(suggested_actions, "respond_to_slack_interaction", respond)
    monkeypatch.setattr(suggested_actions, "is_kitchen_channel", AsyncMock(return_value=False))
    monkeypatch.setattr(
        common, "resolve_slack_channel_context", AsyncMock(return_value=SlackChannelContext())
    )
    monkeypatch.setattr(common, "resolve_slack_thread_id", AsyncMock(return_value="t1"))
    monkeypatch.setattr(common, "get_slack_repo_config", AsyncMock(return_value=None))
    value = {
        "type": suggested_actions.BUTTON_TYPE,
        "action": "accept",
        "kind": GET_REVIEWED.kind,
        "thread_ts": "100.000001",
        "subject": "https://github.com/lc/repo/pull/7",
    }
    interaction = cast(
        SlackInteraction,
        SlackInteraction.parse(
            {
                "type": "block_actions",
                "response_url": "https://hooks.slack.com/actions/T/1/x",
                "channel": {"id": "C1"},
                "user": {"id": "U1"},
                "container": {"type": "message", "is_ephemeral": True},
                "actions": [{"action_id": "x", "value": json.dumps(value), "action_ts": "200.5"}],
            }
        ),
    )
    button = cast(SlackButtonValue, SlackButtonValue.parse(value))
    tasks = _Tasks()

    for _ in range(2):
        await suggested_actions.handle_button(interaction, button, cast(BackgroundTasks, tasks))

    assert len(tasks.tasks) == 1
    request = cast(SlackRequest, tasks.tasks[0][1][0])
    assert (request.channel_id, request.thread_ts, request.thread_id) == ("C1", "100.000001", "t1")
    assert request.event_ts == "200.5"
    assert request.text == "Get https://github.com/lc/repo/pull/7 reviewed."
    assert "<@U1>" in request.turn_context
    respond.assert_awaited_once()


@pytest.mark.parametrize(
    "description", ["", "   ", "x" * 1501, "Fix auth checks.\n\nPlease look at permissions."]
)
@pytest.mark.parametrize("inline_summary", [None, "Generated summary of the change."])
async def test_review_description_validates_before_posting(
    description: str, inline_summary: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = request_modal.ReviewRequestContext(
        pr_url="https://github.com/lc/repo/pull/7",
        user_id="U1",
        channel_id="C1",
        thread_ts="1.0",
        inline_summary=inline_summary,
    )
    interaction = SlackInteraction.model_validate(
        {
            "type": "view_submission",
            "user": {"id": "U1"},
            "view": {
                "callback_id": request_modal.CALLBACK_ID,
                "private_metadata": context.model_dump_json(),
                "state": {"values": {"description": {"description": {"value": description}}}},
            },
        }
    )
    tasks = BackgroundTasks()
    response = await request_modal.handle_submission(interaction, tasks)
    if len(description) > 1500:
        assert response["response_action"] == "errors"
        assert not tasks.tasks
        return
    user = User(identities=[UserIdentity(provider="github", external_id="ada", login="ada")])
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=user))
    monkeypatch.setattr(request_modal, "require_repo_access_for_user", AsyncMock())
    monkeypatch.setattr(
        request_modal.common, "resolve_slack_thread_id", AsyncMock(return_value="thread-1")
    )
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {"source": "slack", "workspace": "default", "github_login": "old-owner"}
    }
    monkeypatch.setattr(request_modal, "langgraph_client", lambda: client)
    post = AsyncMock(return_value=RequestResult(success=True))
    monkeypatch.setattr(request_modal, "request_review", post)
    assert response == {}
    await tasks()
    assert post.call_args.kwargs["human_description"] == (
        description if description.strip() else None
    )
    assert post.call_args.kwargs["inline_summary"] == inline_summary
    assert post.call_args.args[1].requester is user
    assert post.call_args.args[1].thread_id == "thread-1"
    assert post.call_args.args[1].run_config["workspace"] == "default"
    assert post.call_args.args[1].run_config["github_login"] == "ada"
    interaction.user.id = "U2"
    tasks = BackgroundTasks()
    assert (await request_modal.handle_submission(interaction, tasks))["status"] == "ignored"
    assert not tasks.tasks


async def test_review_submission_rechecks_repository_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    user = User(identities=[UserIdentity(provider="github", external_id="ada", login="ada")])
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=user))
    monkeypatch.setattr(
        request_modal,
        "require_repo_access_for_user",
        AsyncMock(side_effect=HTTPException(403, "Repository access denied")),
    )
    post = AsyncMock()
    monkeypatch.setattr(request_modal, "request_review", post)
    feedback = AsyncMock()
    monkeypatch.setattr(request_modal, "post_slack_ephemeral_message", feedback)
    await request_modal.ReviewRequestContext(
        pr_url="https://github.com/lc/repo/pull/7", user_id="U1", channel_id="C1", thread_ts="1.0"
    ).post("Please review this fix")
    post.assert_not_awaited()
    assert feedback.call_args.args[2] == "Repository access denied"
