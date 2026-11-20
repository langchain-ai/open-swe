"""Tests for subdirectory AGENTS.md auto-loading on read_file."""

from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.protocol import BackendProtocol
from langchain_core.messages import ToolMessage, message_to_dict, messages_from_dict
from rt.subdir_agents import SubdirAgentsReadMiddleware


class FakeReadResult:
    def __init__(
        self, *, content: str | None = None, encoding: str = "utf-8", error: str | None = None
    ) -> None:
        self.error = error
        self.file_data = None if content is None else {"content": content, "encoding": encoding}


class FakeBackend(BackendProtocol):
    def __init__(self, results: dict[str, Any]) -> None:
        self.results = results
        self.reads: list[str] = []

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        self.reads.append(file_path)
        result = self.results.get(file_path)
        if isinstance(result, Exception):
            raise result
        if result is None:
            return FakeReadResult(error=f"File '{file_path}': file_not_found")
        return result


def _request(name: str, args: dict[str, Any], backend: FakeBackend) -> Any:
    return SimpleNamespace(
        tool_call={"name": name, "args": args, "id": "call-1"},
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "t1"}}, backend=backend),
        state={"messages": []},
    )


def _ok(name: str, content: str = "file content") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="call-1", status="success", name=name)


def _tool_result(result: object) -> ToolMessage:
    assert isinstance(result, ToolMessage)
    return result


async def test_read_file_appends_applicable_agents_in_root_to_leaf_order() -> None:
    backend = FakeBackend(
        {
            "/repo/AGENTS.md": FakeReadResult(content="root rules"),
            "/repo/pkg/AGENTS.md": FakeReadResult(content="pkg rules"),
        }
    )
    request = _request("read_file", {"file_path": "/repo/pkg/src/app.py"}, backend)

    async def handler(_req: Any) -> ToolMessage:
        return _ok("read_file")

    result = _tool_result(await SubdirAgentsReadMiddleware().awrap_tool_call(request, handler))

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

    request.state["messages"] = [result]
    request.tool_call["args"]["file_path"] = "/repo/pkg/src/other.py"
    again = _tool_result(await SubdirAgentsReadMiddleware().awrap_tool_call(request, handler))
    assert again.content == "file content"
    assert len(backend.reads) == 3


@pytest.mark.parametrize("direct_read", [False, True])
async def test_agents_delivery_is_scoped_to_visible_conversation_history(direct_read: bool) -> None:
    backend = FakeBackend({"/repo/pkg/AGENTS.md": FakeReadResult(content="pkg rules")})
    middleware = SubdirAgentsReadMiddleware()

    async def handler(_req: object) -> ToolMessage:
        return _ok("read_file", "pkg rules" if direct_read else "file content")

    for _conversation in ("parent", "first subagent", "sibling subagent"):
        path = "/repo/pkg/AGENTS.md" if direct_read else "/repo/pkg/a.py"
        first = _tool_result(
            await middleware.awrap_tool_call(
                _request("read_file", {"file_path": path}, backend), handler
            )
        )
        assert "pkg rules" in first.content
        if not direct_read:
            assert "system-reminder" in first.content
        resumed = _request("read_file", {"file_path": "/repo/pkg/b.py"}, backend)
        resumed.state["messages"] = messages_from_dict([message_to_dict(first)])
        second = _tool_result(await SubdirAgentsReadMiddleware().awrap_tool_call(resumed, handler))
        assert "system-reminder" not in second.content

        resumed.state["_summarization_event"] = {"cutoff_index": 1}
        after_summary = _tool_result(await middleware.awrap_tool_call(resumed, handler))
        assert "system-reminder" in after_summary.content
        assert "pkg rules" in after_summary.content


@pytest.mark.parametrize(
    "failure",
    [FakeReadResult(error="unavailable"), RuntimeError("unavailable")],
)
async def test_failed_ancestor_read_is_retried(failure: FakeReadResult | RuntimeError) -> None:
    backend = FakeBackend(
        {
            "/repo/AGENTS.md": FakeReadResult(content="root rules"),
            "/repo/pkg/AGENTS.md": failure,
        }
    )
    middleware = SubdirAgentsReadMiddleware()
    request = _request("read_file", {"file_path": "/repo/pkg/a.py"}, backend)

    async def handler(_req: object) -> ToolMessage:
        return _ok("read_file")

    first = _tool_result(await middleware.awrap_tool_call(request, handler))
    assert "root rules" in first.content
    assert "pkg rules" not in first.content
    request.state["messages"] = [first]
    backend.results["/repo/pkg/AGENTS.md"] = FakeReadResult(content="pkg rules")
    retried = _tool_result(await middleware.awrap_tool_call(request, handler))
    assert "pkg rules" in retried.content
    assert "root rules" not in retried.content


async def test_failed_direct_read_does_not_suppress_ancestor_instructions() -> None:
    backend = FakeBackend({"/repo/pkg/AGENTS.md": FakeReadResult(content="pkg rules")})
    middleware = SubdirAgentsReadMiddleware()
    request = _request("read_file", {"file_path": "/repo/pkg/AGENTS.md"}, backend)

    async def failed(_req: object) -> ToolMessage:
        return _ok("read_file", "unavailable").model_copy(update={"status": "error"})

    first = _tool_result(await middleware.awrap_tool_call(request, failed))
    request.state["messages"] = [first]
    request.tool_call["args"]["file_path"] = "/repo/pkg/a.py"

    async def handler(_req: object) -> ToolMessage:
        return _ok("read_file")

    retried = _tool_result(await middleware.awrap_tool_call(request, handler))
    assert "pkg rules" in retried.content
