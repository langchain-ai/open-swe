from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.slack.http import SlackRequestError

manage_tool = import_module("agent.slack.tools.manage_code_channel")


async def test_sandbox_content_reader_enforces_source_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AsyncMock()
    backend.adownload_files.return_value = [SimpleNamespace(content=b"# Plan")]
    monkeypatch.setattr(
        manage_tool,
        "resolve_sandbox_file",
        AsyncMock(return_value=(backend, "/workspace/plan.md", "/workspace")),
    )

    assert await manage_tool._resolve_content("", "plan.md") == "# Plan"
    with pytest.raises(ValueError, match="Pass content or file_path, not both"):
        await manage_tool._resolve_content("inline", "plan.md")

    backend.adownload_files.return_value = [
        SimpleNamespace(content=b"x" * (manage_tool.VIEW_CONTENT_MAX_BYTES + 1))
    ]
    with pytest.raises(ValueError, match="file_path exceeds Slack's 1 MB view limit"):
        await manage_tool._resolve_content("", "large.html")


async def test_promotion_initializes_status_context_and_runtime_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        "channel_id": "C-origin",
        "thread_ts": "1.000",
        "triggering_event_ts": "1.000",
        "triggering_user_id": "U1",
    }
    client = SimpleNamespace(threads=SimpleNamespace(update=AsyncMock()))

    monkeypatch.setattr(manage_tool, "create_code_channel", AsyncMock(return_value="C-code"))
    monkeypatch.setattr(manage_tool, "rebind_slack_thread", AsyncMock())
    status = AsyncMock(return_value={"ok": True})
    context = AsyncMock(return_value=None)
    commands = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(manage_tool, "set_session_status_result", status)
    monkeypatch.setattr(manage_tool, "set_context_bar", context)
    monkeypatch.setattr(manage_tool, "set_commands", commands)
    monkeypatch.setattr(manage_tool, "invite_to_slack_channel", AsyncMock(return_value=[]))

    result = await manage_tool._create(
        client,
        "thread-1",
        source,
        "Fix flaky tests",
        {"owner": "langchain-ai", "name": "open-swe"},
        invite=[],
    )

    assert result["success"] is True
    status.assert_awaited_once_with("C-code", "processing")
    context.assert_awaited_once()
    commands.assert_awaited_once_with("C-code", manage_tool.DEFAULT_CODE_CHANNEL_COMMANDS)


@pytest.fixture
def promotion(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A promotion Slack agrees to, with the invite call captured."""
    source = {
        "channel_id": "C-origin",
        "thread_ts": "1.000",
        "triggering_event_ts": "1.000",
        "triggering_user_id": "U1",
    }

    invite = AsyncMock(return_value=["U1", "U2"])
    stubs: dict[str, Any] = {
        "create_code_channel": AsyncMock(return_value="C-code"),
        "rebind_slack_thread": AsyncMock(),
        "set_session_status_result": AsyncMock(return_value={"ok": True}),
        "set_context_bar": AsyncMock(return_value=None),
        "set_commands": AsyncMock(return_value={"ok": True}),
        "invite_to_slack_channel": invite,
    }
    for name, mock in stubs.items():
        monkeypatch.setattr(manage_tool, name, mock)
    return {**stubs, "source": source}


async def _promote(invite: list[str]) -> dict[str, Any]:
    return await manage_tool._create(
        SimpleNamespace(threads=SimpleNamespace(update=AsyncMock())),
        "thread-1",
        {
            "channel_id": "C-origin",
            "thread_ts": "1.000",
            "triggering_event_ts": "1.000",
            "triggering_user_id": "U1",
        },
        "Fix flaky tests",
        None,
        invite=invite,
    )


async def test_one_stale_id_does_not_cost_the_others_their_invite(
    promotion: dict[str, Any],
) -> None:
    promotion["invite_to_slack_channel"].side_effect = SlackRequestError(
        "U2 (user_not_found)", invited=["U1"]
    )

    result = await _promote(["U1", "U2"])

    assert result["invited"] == ["U1"]
    assert result["warnings"] == ["Could not invite U2 (user_not_found)"]
