from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from fastapi import BackgroundTasks, HTTPException, Request

from agent.webhooks import slack_routes

FORM = {
    "channel_id": "C-origin",
    "user_id": "U1",
    "text": "fix the flaky login test",
    "response_url": "https://hooks.slack.com/commands/T1/1/abc",
    "team_id": "T1",
    "command": "/code",
}


def _request(form: dict[str, str]) -> Request:
    body = urlencode(form).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/webhooks/slack/code-channel-open",
            "headers": [],
        },
        receive,
    )


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "verify_slack_signature": lambda **_: True,
        "_get_slack_channel_context": AsyncMock(
            return_value={"id": "C-origin", "is_ext_shared": False, "is_pending_ext_shared": False}
        ),
        "slack_channel_allows_operations": lambda _context: True,
        "get_slack_repo_config": AsyncMock(return_value={"owner": "acme", "name": "billing"}),
    }
    for name, mock in calls.items():
        monkeypatch.setattr(slack_routes.common, name, mock)
    return calls


async def _post(form: dict[str, str] | None = None) -> tuple[dict[str, str], BackgroundTasks]:
    tasks = BackgroundTasks()
    response = await slack_routes.slack_open_code_channel_command(
        _request({**FORM, **(form or {})}), tasks
    )
    return response, tasks


def _queued_command(tasks: BackgroundTasks) -> dict[str, Any]:
    queued = tasks.tasks[0].args[0]
    assert isinstance(queued, dict)
    return queued


async def test_the_command_is_acknowledged_before_the_work_starts(route: dict[str, Any]) -> None:
    """Slack gives a command three seconds, and opening a channel takes longer."""
    response, tasks = await _post()

    assert response == {"response_type": "ephemeral", "text": "Opening a code channel…"}
    assert len(tasks.tasks) == 1
    command = _queued_command(tasks)
    assert command["channel_id"] == "C-origin"
    assert command["text"] == "fix the flaky login test"
    assert command["repo"] == {"owner": "acme", "name": "billing"}
    assert command["response_url"] == FORM["response_url"]


async def test_an_unsigned_command_is_refused(
    route: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(slack_routes.common, "verify_slack_signature", lambda **_: False)

    with pytest.raises(HTTPException) as refused:
        await _post()

    assert refused.value.status_code == 401


async def test_a_command_with_no_prompt_says_what_to_type(route: dict[str, Any]) -> None:
    response, tasks = await _post({"text": "   "})

    assert "Say what the channel should work on" in response["text"]
    assert tasks.tasks == []


async def test_an_overlong_prompt_is_refused(route: dict[str, Any]) -> None:
    response, tasks = await _post({"text": "x" * 4001})

    assert "too long" in response["text"]
    assert tasks.tasks == []


async def test_a_command_from_an_ineligible_channel_is_refused(
    route: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An externally shared channel is off limits, command or not."""
    monkeypatch.setattr(slack_routes.common, "slack_channel_allows_operations", lambda _c: False)

    response, tasks = await _post()

    assert response["text"] == "Open SWE does not operate in this channel."
    assert tasks.tasks == []


@pytest.mark.parametrize("missing", ["channel_id", "user_id"])
async def test_a_command_without_its_context_is_refused(
    route: dict[str, Any], missing: str
) -> None:
    response, tasks = await _post({missing: ""})

    assert "missing its context" in response["text"]
    assert tasks.tasks == []
