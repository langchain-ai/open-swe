import importlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

attach_tool = importlib.import_module("agent.tools.slack_attach_file")


def _config() -> dict:
    return {
        "configurable": {
            "thread_id": "thread-1",
            "slack_thread": {"channel_id": "C1", "thread_ts": "1.0"},
        }
    }


def _backend(content: bytes = b"test", *, prepare_output: str | None = None) -> MagicMock:
    backend = MagicMock()
    backend.aexecute = AsyncMock(
        side_effect=[
            MagicMock(
                exit_code=0,
                output=prepare_output if prepare_output is not None else str(len(content)),
            ),
            MagicMock(exit_code=0, output=""),
        ]
    )
    backend.adownload_files = AsyncMock(return_value=[{"content": content}])
    return backend


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    backend: MagicMock,
    *,
    active: list[dict | None] | None = None,
) -> AsyncMock:
    monkeypatch.setattr(
        attach_tool,
        "_resolve_sandbox_file",
        AsyncMock(return_value=(backend, "/workspace/test.html", "/workspace")),
    )
    monkeypatch.setattr(attach_tool, "get_config", _config)
    current = active or [
        {"channel_id": "C1", "thread_ts": "1.0"},
        {"channel_id": "C1", "thread_ts": "1.0"},
    ]
    monkeypatch.setattr(
        attach_tool,
        "get_active_slack_thread",
        AsyncMock(side_effect=current),
    )

    @asynccontextmanager
    async def unlocked(*args: object):
        yield

    monkeypatch.setattr(attach_tool, "slack_thread_mutation_lock", unlocked)
    upload = AsyncMock(return_value=("F1", None))
    monkeypatch.setattr(attach_tool, "upload_slack_thread_file", upload)
    return upload


@pytest.mark.asyncio
async def test_slack_attach_file_uploads_to_active_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"<html>test</html>"
    backend = _backend(content)
    upload = _setup(monkeypatch, backend)

    result = await attach_tool.slack_attach_file(
        "test.html", title="Plan", initial_comment="Preview"
    )

    assert result == {"success": True, "file_id": "F1", "filename": "test.html"}
    assert ".open-swe-slack-upload-" in backend.adownload_files.await_args.args[0][0]
    upload.assert_awaited_once_with(
        "C1", "1.0", "test.html", content, title="Plan", initial_comment="Preview"
    )
    assert backend.aexecute.await_count == 2
    assert "rm -f" in backend.aexecute.await_args.args[0]


@pytest.mark.asyncio
async def test_slack_attach_file_rejects_large_file(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend(prepare_output=str(attach_tool._MAX_SLACK_ATTACHMENT_BYTES + 1))
    monkeypatch.setattr(
        attach_tool,
        "_resolve_sandbox_file",
        AsyncMock(return_value=(backend, "/workspace/big.bin", "/workspace")),
    )

    result = await attach_tool.slack_attach_file("big.bin")

    assert result == {"success": False, "error": "file exceeds the 10 MB attachment limit"}
    backend.adownload_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_attach_file_rejects_control_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _backend()
    _setup(monkeypatch, backend)

    result = await attach_tool.slack_attach_file("test.html", title="bad\x00title")

    assert result == {"success": False, "error": "title contains control characters"}
    backend.adownload_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_attach_file_requires_active_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend()
    _setup(monkeypatch, backend, active=[None])

    result = await attach_tool.slack_attach_file("test.html")

    assert result == {"success": False, "error": "Missing active Slack thread in config"}
    backend.adownload_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_slack_attach_file_rejects_thread_move(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend()
    upload = _setup(
        monkeypatch,
        backend,
        active=[
            {"channel_id": "C1", "thread_ts": "1.0"},
            {"channel_id": "C2", "thread_ts": "2.0"},
        ],
    )

    result = await attach_tool.slack_attach_file("test.html")

    assert result == {"success": False, "error": "Slack thread moved; retry the attachment"}
    upload.assert_not_awaited()
