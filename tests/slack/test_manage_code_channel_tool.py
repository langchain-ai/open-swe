from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

manage_tool = import_module("agent.tools.manage_code_channel")
surfaces_slack = import_module("agent.surfaces.slack")


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


def _exec(output: str = "", exit_code: int = 0) -> SimpleNamespace:
    return SimpleNamespace(output=output, exit_code=exit_code)


@pytest.fixture
def creation(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A clean origin checkout and a code channel Slack agrees to create."""
    backend = AsyncMock()
    backend.aexecute.side_effect = lambda command, **_: (
        _exec("agent/fix-flaky") if "rev-parse" in command else _exec()
    )
    calls: dict[str, Any] = {
        "create_code_channel": AsyncMock(return_value=("C-code", None)),
        "get_sandbox_backend": AsyncMock(return_value=backend),
        "spawn_slack_session": AsyncMock(),
        "get_slack_permalink": AsyncMock(return_value="https://slack.example/p1"),
        "aresolve_repo_dir": AsyncMock(return_value="/workspace/open-swe"),
        "archive_code_channel": AsyncMock(return_value=(True, None)),
    }
    calls["spawn_slack_session"].side_effect = lambda _client, **kwargs: SimpleNamespace(
        thread_id=kwargs["thread_id"],
        slack_thread=None,
        run_id="run-1",
        dashboard_url=f"https://web.example/{kwargs['thread_id']}",
    )
    for name, mock in calls.items():
        monkeypatch.setattr(manage_tool, name, mock)

    chrome = {
        "set_session_status": AsyncMock(return_value=True),
        "set_context_bar": AsyncMock(return_value=(True, None)),
        "set_commands": AsyncMock(return_value=({"ok": True}, None)),
    }
    for name, mock in chrome.items():
        monkeypatch.setattr(surfaces_slack, name, mock)
    return {**calls, **chrome, "backend": backend}


def _client() -> SimpleNamespace:
    return SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"pr_urls": ["https://github.com/a/b/pull/7"]}}
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
        configurable={"github_login": "octocat"},
        **{"instructions": "Fix the flaky login test on the pushed branch.", **overrides},
    )


async def test_create_starts_a_separate_session_in_the_channel(creation: dict[str, Any]) -> None:
    result = await _create()

    assert result["success"] is True
    assert result["channel_id"] == "C-code"
    assert result["mention"] == "<#C-code>"

    # Slack takes the session id as an idempotency key, so it is the id the new
    # session is then created with.
    session_id = creation["create_code_channel"].await_args_list[0].kwargs["session_id"]
    assert result["thread_id"] == session_id
    assert session_id != "thread-origin"

    spawn_kwargs = creation["spawn_slack_session"].await_args_list[0].kwargs
    assert spawn_kwargs["thread_id"] == session_id
    destination = spawn_kwargs["destination"]
    assert (destination.channel_id, destination.thread_ts, destination.surface) == (
        "C-code",
        manage_tool.CODE_CHANNEL_SESSION_TS,
        "slack_channel",
    )


async def test_create_leaves_the_originating_thread_bound(creation: dict[str, Any]) -> None:
    """The origin thread keeps its own session; only the task moves."""
    await _create()
    assert not hasattr(manage_tool, "delete_slack_thread_associations")


async def test_create_hands_over_a_self_contained_task(creation: dict[str, Any]) -> None:
    await _create()

    handoff = creation["spawn_slack_session"].await_args_list[0].kwargs["handoff"]
    assert handoff.title == "Fix flaky tests"
    assert handoff.repo == {"owner": "langchain-ai", "name": "open-swe"}
    assert handoff.source_context["spawned_from"] == {
        "thread_id": "thread-origin",
        "channel_id": "C-origin",
        "thread_ts": "1.000",
        "message_ts": "1.000",
    }
    content = handoff.content
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
    creation["create_code_channel"].side_effect = [("C-one", None), ("C-two", None)]

    first = await _create(client, instructions="First task, self-contained.")
    second = await _create(client, instructions="Second task, also self-contained.")

    assert (first["success"], second["success"]) == (True, True)
    assert first["channel_id"] != second["channel_id"]
    assert first["thread_id"] != second["thread_id"]
    session_ids = [
        call.kwargs["session_id"] for call in creation["create_code_channel"].await_args_list
    ]
    assert len(set(session_ids)) == 2
    assert recorded["source_context"]["spawned_code_channels"] == [
        {"channel_id": "C-one", "thread_id": first["thread_id"]},
        {"channel_id": "C-two", "thread_id": second["thread_id"]},
    ]


async def test_create_sets_up_the_channel_chrome(creation: dict[str, Any]) -> None:
    result = await _create()

    creation["set_session_status"].assert_awaited_once_with("C-code", "processing")
    context_channel, items = creation["set_context_bar"].await_args_list[0].args
    assert context_channel == "C-code"
    assert items[0]["label"] == "langchain-ai/open-swe"
    assert creation["set_commands"].await_args_list[0].args == (
        "C-code",
        surfaces_slack.DEFAULT_CODE_CHANNEL_COMMANDS,
    )
    assert "warnings" not in result


async def test_create_needs_instructions_for_a_session_with_no_history(
    creation: dict[str, Any],
) -> None:
    result = await _create(instructions="   ")

    assert result["success"] is False
    assert "instructions is required" in result["error"]
    creation["create_code_channel"].assert_not_awaited()


@pytest.mark.parametrize(
    ("dirty", "unpushed", "expected"),
    [
        ("M agent/server.py", "", "uncommitted changes"),
        ("", "abc1234", "commits that were never pushed"),
        ("M agent/server.py", "abc1234", "uncommitted changes and commits that were never pushed"),
    ],
)
async def test_create_refuses_to_abandon_work_the_new_sandbox_cannot_see(
    creation: dict[str, Any], dirty: str, unpushed: str, expected: str
) -> None:
    def fake_exec(command: str, **_: Any) -> SimpleNamespace:
        if "rev-parse" in command:
            return _exec("agent/fix-flaky")
        if "status --porcelain" in command:
            return _exec(dirty)
        return _exec(unpushed)

    creation["backend"].aexecute.side_effect = fake_exec

    result = await _create()

    assert result["success"] is False
    assert expected in result["error"]
    assert "Push it first" in result["error"]
    creation["create_code_channel"].assert_not_awaited()


async def test_create_allows_a_checkout_it_cannot_inspect(creation: dict[str, Any]) -> None:
    """An unreachable sandbox is not evidence of unpushed work."""
    creation["get_sandbox_backend"].side_effect = RuntimeError("sandbox is gone")

    result = await _create()

    assert result["success"] is True
    assert "No branch was started yet." in (
        creation["spawn_slack_session"].await_args_list[0].kwargs["handoff"].content
    )


async def test_create_archives_the_channel_when_the_session_never_starts(
    creation: dict[str, Any],
) -> None:
    creation["spawn_slack_session"].side_effect = RuntimeError("dispatch exploded")

    result = await _create()

    assert result["success"] is False
    assert result["retryable"] is True
    assert "dispatch exploded" in result["error"]
    creation["archive_code_channel"].assert_awaited_once_with("C-code")


async def test_create_reports_a_channel_slack_refuses(creation: dict[str, Any]) -> None:
    creation["create_code_channel"].return_value = (None, "name_taken")

    result = await _create()

    assert result == {"success": False, "error": "name_taken"}
    creation["spawn_slack_session"].assert_not_awaited()
