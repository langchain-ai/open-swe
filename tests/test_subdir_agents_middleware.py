"""Tests for subdirectory AGENTS.md auto-loading on read_file."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.subdir_agents import SubdirAgentsReadMiddleware
from agent.utils import sandbox_state


class FakeReadResult:
    def __init__(
        self, *, content: str | None = None, encoding: str = "utf-8", error: str | None = None
    ) -> None:
        self.error = error
        self.file_data = None if content is None else {"content": content, "encoding": encoding}


class FakeBackend:
    def __init__(self, results: dict[str, Any]) -> None:
        self.results = results
        self.reads: list[str] = []

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        self.reads.append(file_path)
        result = self.results.get(file_path)
        if isinstance(result, Exception):
            raise result
        if result is None:
            return FakeReadResult(error="File not found")
        return result


def _request(name: str, args: dict[str, Any], thread_id: str = "t1") -> Any:
    return SimpleNamespace(
        tool_call={"name": name, "args": args, "id": "call-1"},
        runtime=SimpleNamespace(config={"configurable": {"thread_id": thread_id}}),
    )


def _ok(name: str, content: str = "file content") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="call-1", status="success", name=name)


@pytest.fixture
def register_backend():
    registered: list[str] = []

    def _register(thread_id: str, backend: Any) -> Any:
        sandbox_state.SANDBOX_BACKENDS[thread_id] = backend
        registered.append(thread_id)
        return backend

    yield _register
    for thread_id in registered:
        sandbox_state.SANDBOX_BACKENDS.pop(thread_id, None)


async def test_read_file_appends_applicable_agents_in_root_to_leaf_order(register_backend) -> None:
    backend = FakeBackend(
        {
            "/repo/AGENTS.md": FakeReadResult(content="root rules"),
            "/repo/pkg/AGENTS.md": FakeReadResult(content="pkg rules"),
        }
    )
    register_backend("t1", backend)
    request = _request("read_file", {"file_path": "/repo/pkg/src/app.py"})

    async def handler(_req: Any) -> ToolMessage:
        return _ok("read_file")

    result = await SubdirAgentsReadMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result.content, str)
    assert "<system-reminder>" in result.content
    assert "Loaded applicable AGENTS.md instructions for `/repo/pkg/src/app.py`" in result.content
    assert result.content.index("Instructions from: /repo/AGENTS.md") < result.content.index(
        "Instructions from: /repo/pkg/AGENTS.md"
    )
    assert "root rules" in result.content
    assert "pkg rules" in result.content
    assert backend.reads == [
        "/repo/AGENTS.md",
        "/repo/pkg/AGENTS.md",
        "/repo/pkg/src/AGENTS.md",
    ]


async def test_read_file_does_not_reload_same_agents_file(register_backend) -> None:
    backend = FakeBackend({"/repo/pkg/AGENTS.md": FakeReadResult(content="pkg rules")})
    register_backend("t1", backend)
    middleware = SubdirAgentsReadMiddleware()

    async def handler(_req: Any) -> ToolMessage:
        return _ok("read_file")

    first = await middleware.awrap_tool_call(
        _request("read_file", {"file_path": "/repo/pkg/a.py"}), handler
    )
    second = await middleware.awrap_tool_call(
        _request("read_file", {"file_path": "/repo/pkg/b.py"}), handler
    )

    assert isinstance(first.content, str)
    assert "pkg rules" in first.content
    assert isinstance(second.content, str)
    assert "system-reminder" not in second.content
    assert backend.reads == ["/repo/AGENTS.md", "/repo/pkg/AGENTS.md"]


async def test_reading_agents_md_directly_does_not_append_reminder(register_backend) -> None:
    backend = FakeBackend({"/repo/pkg/AGENTS.md": FakeReadResult(content="pkg rules")})
    register_backend("t1", backend)
    request = _request("read_file", {"file_path": "/repo/pkg/AGENTS.md"})

    async def handler(_req: Any) -> ToolMessage:
        return _ok("read_file", "pkg rules")

    result = await SubdirAgentsReadMiddleware().awrap_tool_call(request, handler)

    assert result.content == "pkg rules"
    assert backend.reads == []


async def test_non_read_file_tool_is_untouched(register_backend) -> None:
    backend = FakeBackend({"/repo/AGENTS.md": FakeReadResult(content="root rules")})
    register_backend("t1", backend)
    request = _request("edit_file", {"file_path": "/repo/a.py"})

    async def handler(_req: Any) -> ToolMessage:
        return _ok("edit_file")

    result = await SubdirAgentsReadMiddleware().awrap_tool_call(request, handler)

    assert result.content == "file content"
    assert backend.reads == []


async def test_missing_backend_is_graceful() -> None:
    request = _request("read_file", {"file_path": "/repo/a.py"}, thread_id="absent-thread")

    async def handler(_req: Any) -> ToolMessage:
        return _ok("read_file")

    result = await SubdirAgentsReadMiddleware().awrap_tool_call(request, handler)

    assert result.content == "file content"


def test_sync_tool_call_is_not_supported() -> None:
    request = _request("read_file", {"file_path": "/repo/a.py"})

    def handler(_req: Any) -> ToolMessage:
        return _ok("read_file")

    with pytest.raises(NotImplementedError):
        SubdirAgentsReadMiddleware().wrap_tool_call(request, handler)
