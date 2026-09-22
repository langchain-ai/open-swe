import importlib
import json
import sys
import types
from contextlib import asynccontextmanager
from typing import Any

from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

sandbox_output = importlib.import_module("agent.tools.sandbox_output")
web_search_tool = importlib.import_module("agent.tools.web_search")


def _decode_jsonl(content: str) -> str:
    return "".join(json.loads(line)["text"] for line in content.splitlines())


class FakeExa:
    result = ""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search_and_contents(self, *args: Any, **kwargs: Any) -> str:
        return self.result

    def search(self, *args: Any, **kwargs: Any) -> str:
        return self.result


def test_chunk_output_as_jsonl_is_lossless_and_bounds_source_lines() -> None:
    content = "prefix\n" + "x" * 10_000 + "\nsuffix"

    encoded = sandbox_output.chunk_output_as_jsonl(content)

    assert _decode_jsonl(encoded) == content
    assert max(map(len, encoded.splitlines())) < 5_000


async def test_write_sandbox_output_uses_current_thread_backend(monkeypatch) -> None:
    writes: list[tuple[str, str]] = []

    class Backend:
        async def awrite(self, path: str, content: str) -> dict[str, None]:
            writes.append((path, content))
            return {"error": None}

    backend = Backend()

    async def fake_get_backend(thread_id: str) -> Backend:
        assert thread_id == "thread-123"
        return backend

    async def fake_resolve_work_dir(value: Backend) -> str:
        assert value is backend
        return "/workspace"

    monkeypatch.setattr(
        "agent.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-123"}}
    )
    monkeypatch.setattr(sandbox_output, "get_sandbox_backend", fake_get_backend)
    monkeypatch.setattr(sandbox_output, "resolve_sandbox_work_dir", fake_resolve_work_dir)

    path = await sandbox_output.write_sandbox_output("web-search", "full results", "txt")

    assert path.startswith("/workspace/web-search-")
    assert path.endswith(".txt")
    assert writes == [(path, "full results")]


async def test_web_search_saves_results_and_returns_only_path(monkeypatch) -> None:
    raw_results = "untrusted result\n" + "x" * 200_000
    FakeExa.result = raw_results
    monkeypatch.setitem(sys.modules, "exa_py", types.SimpleNamespace(Exa=FakeExa))
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    writes: list[tuple[str, str, str]] = []

    async def fake_write(tool_name: str, content: str, extension: str) -> str:
        writes.append((tool_name, content, extension))
        return "/workspace/web-search-result.jsonl"

    monkeypatch.setattr(web_search_tool, "write_sandbox_output", fake_write)

    result = await web_search_tool.web_search("python docs")

    assert result == {
        "success": True,
        "results_path": "/workspace/web-search-result.jsonl",
        "results": None,
        "result_chars": len(raw_results),
        "error": None,
    }
    assert writes[0][0::2] == ("web-search", "jsonl")
    assert _decode_jsonl(writes[0][1]) == raw_results
    assert raw_results not in str(result)


async def test_web_search_returns_bounded_inline_results_without_sandbox(monkeypatch) -> None:
    raw_results = "search marker " + "x" * 200_000
    FakeExa.result = raw_results
    monkeypatch.setitem(sys.modules, "exa_py", types.SimpleNamespace(Exa=FakeExa))
    monkeypatch.setenv("EXA_API_KEY", "test-key")

    async def fail_write(tool_name: str, content: str, extension: str) -> str:
        raise ValueError("Missing sandbox_id in thread metadata for review-chat")

    monkeypatch.setattr(web_search_tool, "write_sandbox_output", fail_write)

    result = await web_search_tool.web_search("python docs")

    assert result["success"] is True
    assert result["results_path"] is None
    assert result["result_chars"] == len(raw_results)
    assert result["results"].startswith("search marker ")
    assert "[results truncated: 100000/200014 chars]" in result["results"]
    assert len(result["results"]) < 100_100
    assert raw_results not in str(result)


async def test_web_search_parallel_uses_canonical_tool_without_exa_key(monkeypatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    calls: list[tuple[str, dict[str, object]]] = []

    class Session:
        async def initialize(self) -> None:
            pass

        async def list_tools(self) -> ListToolsResult:
            return ListToolsResult(
                tools=[
                    Tool(name="web_search", inputSchema={"type": "object"}),
                    Tool(name="web_fetch", inputSchema={"type": "object"}),
                ]
            )

        async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
            calls.append((name, arguments))
            if name == "web_search":
                return CallToolResult(
                    content=[],
                    structuredContent={
                        "session_id": "search-session",
                        "results": [
                            {"url": "https://example.com/one", "excerpts": ["One"]},
                            {"url": "https://example.com/two", "excerpts": ["Two"]},
                        ],
                    },
                )
            return CallToolResult(
                content=[],
                structuredContent={
                    "results": [{"url": "https://example.com/one", "full_content": "Full page"}],
                    "errors": [],
                },
            )

    @asynccontextmanager
    async def fake_session(connection):
        assert connection["headers"] == {"User-Agent": "open-swe"}
        assert connection["url"] == "https://search.parallel.ai/mcp"
        yield Session()

    async def no_sandbox(*args: object) -> str:
        raise ValueError("No sandbox in this test")

    monkeypatch.setattr(web_search_tool, "create_session", fake_session)
    monkeypatch.setattr(web_search_tool, "write_sandbox_output", no_sandbox)

    result = await web_search_tool.web_search(
        "python docs", num_results=1, include_contents=True, provider="parallel"
    )

    assert result["success"] is True
    payload = json.loads(result["results"])
    assert payload["results"] == [
        {"url": "https://example.com/one", "excerpts": ["One"], "full_content": "Full page"}
    ]
    assert calls == [
        ("web_search", {"objective": "python docs", "search_queries": ["python docs"]}),
        (
            "web_fetch",
            {
                "urls": ["https://example.com/one"],
                "objective": "python docs",
                "full_content": True,
                "session_id": "search-session",
            },
        ),
    ]


async def test_web_search_parallel_surfaces_mcp_tool_errors(monkeypatch) -> None:
    class Session:
        async def initialize(self) -> None:
            pass

        async def list_tools(self) -> ListToolsResult:
            return ListToolsResult(
                tools=[
                    Tool(name="web_search", inputSchema={"type": "object"}),
                    Tool(name="web_fetch", inputSchema={"type": "object"}),
                ]
            )

        async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
            return CallToolResult(
                isError=True, content=[TextContent(type="text", text="Rate limited")]
            )

    @asynccontextmanager
    async def fake_session(connection):
        yield Session()

    monkeypatch.setattr(web_search_tool, "create_session", fake_session)

    result = await web_search_tool.web_search("python docs", provider="parallel")

    assert result["success"] is False
    assert result["error"] == "RuntimeError: Rate limited"
