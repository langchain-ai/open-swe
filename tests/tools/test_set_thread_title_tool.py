from __future__ import annotations

import importlib
from typing import Any

import pytest

set_thread_title_tool = importlib.import_module("agent.tools.set_thread_title")


class _FakeThreadsClient:
    def __init__(self, captured: dict[str, Any], error: Exception | None = None) -> None:
        self.captured = captured
        self.error = error

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.captured.update({"thread_id": thread_id, "metadata": metadata})


class _FakeClient:
    def __init__(self, captured: dict[str, Any], error: Exception | None = None) -> None:
        self.threads = _FakeThreadsClient(captured, error)


def _config() -> dict[str, Any]:
    return {"configurable": {"thread_id": "thread-123"}}


async def test_set_thread_title_updates_current_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(set_thread_title_tool, "get_config", _config)
    monkeypatch.setattr(set_thread_title_tool, "get_client", lambda: _FakeClient(captured))

    result = await set_thread_title_tool.set_thread_title("  Add thread titles  ")

    assert result == {"success": True, "title": "Add thread titles"}
    assert captured == {
        "thread_id": "thread-123",
        "metadata": {"title": "Add thread titles"},
    }


@pytest.mark.parametrize(
    ("title", "error"),
    [
        ("   ", "title is required"),
        ("x" * 81, "title exceeds the 80 character limit"),
    ],
)
async def test_set_thread_title_validates_title(
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    error: str,
) -> None:
    monkeypatch.setattr(set_thread_title_tool, "get_config", _config)
    monkeypatch.setattr(
        set_thread_title_tool,
        "get_client",
        lambda: pytest.fail("get_client should not be called"),
    )

    result = await set_thread_title_tool.set_thread_title(title)

    assert result == {"success": False, "error": error}


async def test_set_thread_title_requires_current_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(set_thread_title_tool, "get_config", lambda: {"configurable": {}})

    result = await set_thread_title_tool.set_thread_title("Add thread titles")

    assert result == {
        "success": False,
        "error": "Missing configurable.thread_id in config",
    }


async def test_set_thread_title_handles_update_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(set_thread_title_tool, "get_config", _config)
    monkeypatch.setattr(
        set_thread_title_tool,
        "get_client",
        lambda: _FakeClient({}, RuntimeError("backend unavailable")),
    )

    result = await set_thread_title_tool.set_thread_title("Add thread titles")

    assert result == {"success": False, "error": "Could not update thread title"}
