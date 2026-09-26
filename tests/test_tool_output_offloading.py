import importlib
import json
import sys
import types
from typing import Any

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
        "status": "success",
        "retryable": False,
        "results_path": "/workspace/web-search-result.jsonl",
        "results": None,
        "result_chars": len(raw_results),
        "error": None,
    }
    assert writes[0][0::2] == ("web-search", "jsonl")
    assert _decode_jsonl(writes[0][1]) == raw_results
    assert raw_results not in str(result)


async def test_web_search_classifies_provider_credits_exhausted(monkeypatch) -> None:
    class CreditsExhaustedError(Exception):
        status_code = 402

    class FailingExa(FakeExa):
        def search_and_contents(self, *args: Any, **kwargs: Any) -> str:
            raise CreditsExhaustedError("payment required: buy more credits in the dashboard")

    monkeypatch.setitem(sys.modules, "exa_py", types.SimpleNamespace(Exa=FailingExa))
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    monkeypatch.setattr(web_search_tool, "_provider_unavailable_reported", False)
    reports: list[tuple[str, list[str]]] = []

    async def fake_report(problem_description: str, keywords: list[str]) -> dict[str, str]:
        reports.append((problem_description, keywords))
        return {"report_id": "report-1"}

    monkeypatch.setattr(web_search_tool, "report_platform_issue", fake_report)

    result = await web_search_tool.web_search("python docs")

    assert result == {
        "success": False,
        "status": "search_unavailable",
        "retryable": False,
        "results_path": None,
        "results": None,
        "result_chars": 0,
        "error": "Web search is unavailable for this deployment (search provider returned 402). "
        "Do not retry this tool in this turn; if the answer depends on web evidence, say the web "
        "could not be consulted and fall back to fetch_url on a known URL.",
    }
    assert "credits" not in result["error"]
    assert len(reports) == 1


async def test_web_search_classifies_provider_server_error_as_retryable(monkeypatch) -> None:
    class ServerError(Exception):
        status = 503

    class FailingExa(FakeExa):
        def search_and_contents(self, *args: Any, **kwargs: Any) -> str:
            raise ServerError("status code 503")

    monkeypatch.setitem(sys.modules, "exa_py", types.SimpleNamespace(Exa=FailingExa))
    monkeypatch.setenv("EXA_API_KEY", "test-key")

    result = await web_search_tool.web_search("python docs")

    assert result == {
        "success": False,
        "status": "search_error",
        "retryable": True,
        "results_path": None,
        "results": None,
        "result_chars": 0,
        "error": "Web search temporarily failed. The tool may be retried.",
    }


async def test_web_search_sanitizes_vendor_billing_error(monkeypatch) -> None:
    class ProviderError(Exception):
        pass

    class FailingExa(FakeExa):
        def search_and_contents(self, *args: Any, **kwargs: Any) -> str:
            raise ProviderError("Exa says upgrade your plan in the billing dashboard")

    monkeypatch.setitem(sys.modules, "exa_py", types.SimpleNamespace(Exa=FailingExa))
    monkeypatch.setenv("EXA_API_KEY", "test-key")

    result = await web_search_tool.web_search("python docs")

    assert result["status"] == "search_error"
    assert result["retryable"] is False
    assert result["error"] == "ProviderError: provider error details redacted"
    assert "billing" not in result["error"]


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
