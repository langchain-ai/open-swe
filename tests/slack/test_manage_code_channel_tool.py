from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

manage_tool = import_module("agent.tools.manage_code_channel")


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

    @asynccontextmanager
    async def locked(*_args: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield source

    monkeypatch.setattr(
        manage_tool, "create_code_channel", AsyncMock(return_value=("C-code", None))
    )
    monkeypatch.setattr(manage_tool, "slack_thread_mutation_lock", locked)
    monkeypatch.setattr(manage_tool, "bind_slack_thread_id", AsyncMock())
    monkeypatch.setattr(manage_tool, "delete_slack_thread_associations", AsyncMock())
    status = AsyncMock(return_value=({"ok": True}, None))
    context = AsyncMock(return_value=(True, None))
    commands = AsyncMock(return_value=({"ok": True}, None))
    monkeypatch.setattr(manage_tool, "set_session_status_result", status)
    monkeypatch.setattr(manage_tool, "set_context_bar", context)
    monkeypatch.setattr(manage_tool, "set_commands", commands)

    result = await manage_tool._create(
        client,
        "thread-1",
        source,
        "Fix flaky tests",
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    assert result["success"] is True
    status.assert_awaited_once_with("C-code", "processing")
    context.assert_awaited_once()
    commands.assert_awaited_once_with("C-code", manage_tool.DEFAULT_CODE_CHANNEL_COMMANDS)
