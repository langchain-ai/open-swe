"""Tests for ToolArtifactMiddleware diff stamping on edit_file/write_file."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware.tool_artifact import ToolArtifactMiddleware
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

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        self.reads.append(file_path)
        result = self.results.get(file_path)
        if isinstance(result, Exception):
            raise result
        if result is None:
            return FakeReadResult(error="File not found")
        return result

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        return self.read(file_path, offset, limit)


def _request(name: str, args: dict[str, Any], thread_id: str = "t1") -> Any:
    return SimpleNamespace(
        tool_call={"name": name, "args": args, "id": "call-1"},
        runtime=SimpleNamespace(config={"configurable": {"thread_id": thread_id}}),
    )


def _ok(name: str) -> ToolMessage:
    return ToolMessage(content="ok", tool_call_id="call-1", status="success", name=name)


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


async def test_edit_file_stamps_full_file_diff(register_backend) -> None:
    backend = FakeBackend({"/repo/a.py": FakeReadResult(content="line1\nOLD\nline3\n")})
    register_backend("t1", backend)
    request = _request(
        "edit_file", {"file_path": "/repo/a.py", "old_string": "OLD", "new_string": "NEW"}
    )

    async def handler(_req: Any) -> ToolMessage:
        return _ok("edit_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact == {
        "diff": {
            "filePath": "/repo/a.py",
            "originalContent": "line1\nOLD\nline3\n",
            "newContent": "line1\nNEW\nline3\n",
            "isNewFile": False,
        }
    }
    assert backend.reads == ["/repo/a.py"]


async def test_edit_file_replace_all(register_backend) -> None:
    backend = FakeBackend({"/repo/a.py": FakeReadResult(content="x x x")})
    register_backend("t1", backend)
    request = _request(
        "edit_file",
        {"file_path": "/repo/a.py", "old_string": "x", "new_string": "y", "replace_all": True},
    )

    async def handler(_req: Any) -> ToolMessage:
        return _ok("edit_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact["diff"]["newContent"] == "y y y"


async def test_edit_file_missing_old_string_skips(register_backend) -> None:
    backend = FakeBackend({"/repo/a.py": FakeReadResult(content="nothing here")})
    register_backend("t1", backend)
    request = _request(
        "edit_file", {"file_path": "/repo/a.py", "old_string": "OLD", "new_string": "NEW"}
    )

    async def handler(_req: Any) -> ToolMessage:
        return _ok("edit_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact is None


async def test_write_file_new_file(register_backend) -> None:
    backend = FakeBackend({"/repo/new.py": FakeReadResult(error="File not found")})
    register_backend("t1", backend)
    request = _request("write_file", {"file_path": "/repo/new.py", "content": "hello\n"})

    async def handler(_req: Any) -> ToolMessage:
        return _ok("write_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact == {
        "diff": {
            "filePath": "/repo/new.py",
            "originalContent": None,
            "newContent": "hello\n",
            "isNewFile": True,
        }
    }


async def test_write_file_overwrite(register_backend) -> None:
    backend = FakeBackend({"/repo/x.py": FakeReadResult(content="old content\n")})
    register_backend("t1", backend)
    request = _request("write_file", {"file_path": "/repo/x.py", "content": "new content\n"})

    async def handler(_req: Any) -> ToolMessage:
        return _ok("write_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact["diff"]["originalContent"] == "old content\n"
    assert result.artifact["diff"]["newContent"] == "new content\n"
    assert result.artifact["diff"]["isNewFile"] is False


async def test_non_edit_tool_is_untouched(register_backend) -> None:
    backend = FakeBackend({})
    register_backend("t1", backend)
    request = _request("read_file", {"file_path": "/repo/a.py"})

    async def handler(_req: Any) -> ToolMessage:
        return _ok("read_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact is None
    assert backend.reads == []


async def test_error_result_is_not_stamped(register_backend) -> None:
    backend = FakeBackend({"/repo/a.py": FakeReadResult(content="OLD")})
    register_backend("t1", backend)
    request = _request(
        "edit_file", {"file_path": "/repo/a.py", "old_string": "OLD", "new_string": "NEW"}
    )

    async def handler(_req: Any) -> ToolMessage:
        return ToolMessage(content="boom", tool_call_id="call-1", status="error", name="edit_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact is None


async def test_missing_backend_is_graceful() -> None:
    request = _request(
        "edit_file",
        {"file_path": "/repo/a.py", "old_string": "OLD", "new_string": "NEW"},
        thread_id="absent-thread",
    )

    async def handler(_req: Any) -> ToolMessage:
        return _ok("edit_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact is None


async def test_binary_read_skips(register_backend) -> None:
    backend = FakeBackend({"/repo/img.png": FakeReadResult(content="QkFTRTY0", encoding="base64")})
    register_backend("t1", backend)
    request = _request(
        "edit_file", {"file_path": "/repo/img.png", "old_string": "x", "new_string": "y"}
    )

    async def handler(_req: Any) -> ToolMessage:
        return _ok("edit_file")

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact is None


async def test_existing_artifact_is_merged(register_backend) -> None:
    backend = FakeBackend({"/repo/a.py": FakeReadResult(content="OLD\n")})
    register_backend("t1", backend)
    request = _request(
        "edit_file", {"file_path": "/repo/a.py", "old_string": "OLD", "new_string": "NEW"}
    )

    async def handler(_req: Any) -> ToolMessage:
        message = _ok("edit_file")
        message.artifact = {"existing": "kept"}
        return message

    result = await ToolArtifactMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.artifact["existing"] == "kept"
    assert result.artifact["diff"]["newContent"] == "NEW\n"
