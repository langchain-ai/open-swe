from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.run_config import RunConfig

manage_tool = import_module("agent.slack.tools.manage_code_channel")
surfaces_slack = import_module("agent.slack.surfaces.channel")


@pytest.mark.parametrize(
    ("metadata", "fallback", "expected"),
    [
        (
            {"title": "  Match code channel and thread titles.  ", "title_seed": None},
            "Different title",
            "Match code channel and thread titles.",
        ),
        (
            {"title": "Original Slack message", "title_seed": "Original Slack message"},
            "Fallback title",
            "Fallback title",
        ),
        ({}, "Fallback title", "Fallback title"),
        # Legacy metadata written before title generation stored a seed: a
        # title without a title_seed key is not provably generated.
        ({"title": "Legacy hand-written title"}, "Fallback title", "Fallback title"),
    ],
)
async def test_code_channel_title_prefers_thread_title(
    metadata: dict[str, Any], fallback: str, expected: str
) -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}))
    )

    assert await manage_tool._code_channel_title(client, "thread-1", fallback) == expected


async def test_sandbox_content_reader_enforces_source_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AsyncMock()
    backend.adownload_files.return_value = [SimpleNamespace(content=b"# Plan")]
    monkeypatch.setattr(
        manage_tool,
        "_resolve_sandbox_file",
        AsyncMock(return_value=(backend, "/workspace/plan.md", "/workspace")),
    )

    content, error = await manage_tool._resolve_content("", "plan.md")
    conflict_content, conflict_error = await manage_tool._resolve_content("inline", "plan.md")

    assert (content, error) == ("# Plan", None)
    assert conflict_content == ""
    assert conflict_error == "Pass content or file_path, not both"

    backend.adownload_files.return_value = [
        SimpleNamespace(content=b"x" * (manage_tool.VIEW_CONTENT_MAX_BYTES + 1))
    ]
    _, size_error = await manage_tool._resolve_content("", "large.html")
    assert size_error == "file_path exceeds Slack's 1 MB view limit"


ORIGIN = {
    "channel_id": "C-origin",
    "thread_ts": "1.000",
    "triggering_event_ts": "1.000",
    "triggering_user_id": "U1",
    "triggering_user_name": "Ramon",
}


def _opened(channel_id: str = "C-code") -> SimpleNamespace:
    """What `open_code_channel` hands back once the session is running."""
    return SimpleNamespace(
        channel_id=channel_id,
        session=SimpleNamespace(
            thread_id=f"thread-{channel_id}",
            slack_thread=None,
            run_id="run-1",
            dashboard_url=f"https://web.example/{channel_id}",
        ),
        invited=["U1"],
        warnings=[],
    )


@pytest.fixture
def creation(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A code channel Slack agrees to create."""
    calls: dict[str, Any] = {
        "open_code_channel": AsyncMock(),
        "get_slack_permalink": AsyncMock(return_value="https://slack.example/p1"),
    }
    calls["open_code_channel"].side_effect = lambda _client, **_kwargs: _opened()
    for name, mock in calls.items():
        monkeypatch.setattr(manage_tool, name, mock)

    chrome = {
        "set_session_status": AsyncMock(return_value=True),
        "set_context_bar": AsyncMock(return_value=(True, None)),
        "set_commands": AsyncMock(return_value=({"ok": True}, None)),
    }
    for name, mock in chrome.items():
        monkeypatch.setattr(surfaces_slack, name, mock)
    return {**calls, **chrome}


def _client() -> SimpleNamespace:
    return SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={
                    "metadata": {
                        "branch_name": "agent/fix-flaky",
                        "pr_urls": ["https://github.com/a/b/pull/7"],
                    }
                }
            ),
            update=AsyncMock(),
        )
    )


async def _create(client: Any = None, **overrides: Any) -> dict[str, Any]:
    return await manage_tool._create(
        client or _client(),
        "thread-origin",
        ORIGIN,
        "Fix flaky tests",
        {"owner": "langchain-ai", "name": "open-swe"},
        cfg=RunConfig(github_login="octocat"),
        **{
            "instructions": "Fix the flaky login test on the pushed branch.",
            "invite": ["U1"],
            **overrides,
        },
    )


async def test_create_starts_a_separate_session_in_the_channel(creation: dict[str, Any]) -> None:
    result = await _create()

    assert result["success"] is True
    assert result["channel_id"] == "C-code"
    assert result["mention"] == "<#C-code>"

    assert result["thread_id"] == "thread-C-code"

    # The channel is opened against the message that asked for it, so Slack lets
    # the origin channel's members in and invites whoever asked.
    opened = creation["open_code_channel"].await_args_list[0].kwargs
    assert (opened["origin_channel_id"], opened["origin_message_ts"]) == ("C-origin", "1.000")


async def test_create_leaves_the_originating_thread_bound(creation: dict[str, Any]) -> None:
    """The origin thread keeps its own session; only the task moves."""
    await _create()
    assert not hasattr(manage_tool, "delete_slack_thread_associations")


async def test_create_hands_over_a_self_contained_task(creation: dict[str, Any]) -> None:
    await _create()

    opened = creation["open_code_channel"].await_args_list[0].kwargs
    assert opened["title"] == "Fix flaky tests"
    assert opened["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert opened["source_context"]["spawned_from"] == {
        "thread_id": "thread-origin",
        "channel_id": "C-origin",
        "thread_ts": "1.000",
        "message_ts": "1.000",
    }
    content = opened["content"]
    assert "Fix the flaky login test on the pushed branch." in content
    assert "langchain-ai/open-swe" in content
    assert "`agent/fix-flaky`" in content
    assert "https://github.com/a/b/pull/7" in content
    assert "https://slack.example/p1" in content
    assert "fresh sandbox" in content


async def test_create_records_the_channel_on_the_origin_thread(creation: dict[str, Any]) -> None:
    client = _client()
    result = await _create(client)

    update = client.threads.update.await_args_list[0].kwargs
    assert update["thread_id"] == "thread-origin"
    assert update["metadata"]["source_context"]["spawned_code_channels"] == [
        {"channel_id": "C-code", "thread_id": result["thread_id"]}
    ]


async def test_a_thread_can_hand_out_as_many_channels_as_it_has_tasks(
    creation: dict[str, Any],
) -> None:
    """The sessions are disconnected, so nothing about one constrains the next."""
    recorded: dict[str, Any] = {"pr_urls": []}
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(return_value={"metadata": recorded}),
            update=AsyncMock(side_effect=lambda *, thread_id, metadata: recorded.update(metadata)),
        )
    )
    channels = iter(("C-one", "C-two"))
    creation["open_code_channel"].side_effect = lambda _client, **_kwargs: _opened(next(channels))

    first = await _create(client, instructions="First task, self-contained.")
    second = await _create(client, instructions="Second task, also self-contained.")

    assert (first["success"], second["success"]) == (True, True)
    assert first["channel_id"] != second["channel_id"]
    assert first["thread_id"] != second["thread_id"]
    assert recorded["source_context"]["spawned_code_channels"] == [
        {"channel_id": "C-one", "thread_id": first["thread_id"]},
        {"channel_id": "C-two", "thread_id": second["thread_id"]},
    ]


async def test_create_needs_instructions_for_a_session_with_no_history(
    creation: dict[str, Any],
) -> None:
    result = await _create(instructions="   ")

    assert result["success"] is False
    assert "instructions is required" in result["error"]
    creation["open_code_channel"].assert_not_awaited()


@pytest.mark.parametrize("retryable", [True, False])
async def test_create_reports_a_channel_that_could_not_be_opened(
    creation: dict[str, Any], retryable: bool
) -> None:
    creation["open_code_channel"].side_effect = manage_tool.CodeChannelError(
        "name_taken", retryable=retryable
    )

    result = await _create()

    assert result["success"] is False
    assert result["error"] == "name_taken"
    assert result.get("retryable") is (True if retryable else None)
