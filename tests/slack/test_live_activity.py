import importlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openswe.slack.live_activity import LiveActivity, SlackLiveMessage
from openswe.slack.records import SlackRun
from openswe.transcript.snapshot import MessageView, ToolCallView

live_activity = importlib.import_module("openswe.slack.live_activity")

_TURN = uuid4()
_START = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def _thought(at: int, reasoning: str) -> MessageView:
    return MessageView(
        message_id=f"m{at}",
        turn_id=_TURN,
        role="ai",
        text="",
        reasoning=reasoning,
        namespace=[],
        sender=None,
        attachments=None,
        usage=None,
        created_at=_START + timedelta(seconds=at),
    )


def _call(
    at: int, status: str = "completed", *, namespace: list[str] | None = None
) -> ToolCallView:
    return ToolCallView(
        tool_call_id=f"read-{at}",
        turn_id=_TURN,
        message_id=None,
        name="read_file",
        input={"file_path": f"/repo/file_{at}.py"},
        status=status,
        output_preview=None,
        output_truncated=False,
        has_output=False,
        attachments=None,
        namespace=namespace or [],
        started_at=_START + timedelta(seconds=at),
        ended_at=None,
    )


def test_keeps_the_newest_work_that_fits_ten_lines() -> None:
    passage = "Now I'm reading the queue store. " * 12
    activity = LiveActivity(
        [
            _thought(1, "Looking around"),
            _thought(7, f"**Checking the write path**\n\nAn old paragraph.\n\n{passage}"),
        ],
        [
            _call(0),
            *(_call(at) for at in range(2, 7)),
            _call(8, "running"),
            _call(9, namespace=["subagent"]),
        ],
    )

    assert [(entry.kind, entry.title, entry.lines) for entry in activity.entries] == [
        *(("tool", f"Reading file_{at}.py", 1) for at in range(2, 7)),
        ("reasoning", "Checking the write path", 4),
        ("tool", "Reading file_8.py", 1),
    ]
    assert activity.entries[5].body == passage.strip()
    assert activity.action_count == 7


def test_reads_a_streaming_heading_as_text() -> None:
    entry = LiveActivity.thought("**Planning**\n\nFirst I'll read it.\n**Next st")

    assert (entry.title, entry.body) == ("Planning", "First I'll read it. Next st")


def test_shows_only_the_program_a_command_runs() -> None:
    activity = LiveActivity(
        [],
        [
            ToolCallView(
                tool_call_id="shell",
                turn_id=_TURN,
                message_id=None,
                name="execute",
                input={"command": "GH_TOKEN=ghp_secret /usr/bin/curl -H 'Authorization: x' api"},
                status="running",
                output_preview=None,
                output_truncated=False,
                has_output=False,
                attachments=None,
                namespace=[],
                started_at=_START,
                ended_at=None,
            )
        ],
    )

    [entry] = activity.entries
    assert entry.body == "curl"
    assert "secret" not in str(entry.block())


@pytest.mark.usefixtures("registry_db")
async def test_stop_acts_only_on_a_runs_current_live_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = AsyncMock()
    monkeypatch.setattr(live_activity, "process_agent_session_stopped", stopped)
    await SlackRun.set_live_message("C1", "1.0", "run-1", "1.1")
    click = {
        "channel_id": "C1",
        "thread_ts": "1.0",
        "user_id": "U1",
        "team_id": "T1",
        "action_ts": "2.0",
    }

    await SlackLiveMessage.stop_clicked(message_ts="1.9", **click)
    stopped.assert_not_awaited()

    await SlackLiveMessage.stop_clicked(message_ts="1.1", **click)
    stopped.assert_awaited_once_with(
        {"channel": "C1", "thread_ts": "1.0", "user": "U1"}, "live-activity-stop:C1:1.1:2.0", "T1"
    )
