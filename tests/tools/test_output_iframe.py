import importlib
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

iframe_tool = importlib.import_module("agent.tools.output_iframe")


class _Backend:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.downloaded: list[str] = []

    async def adownload_files(self, paths: list[str]) -> list[dict[str, Any]]:
        path = paths[0]
        self.downloaded.append(path)
        if path not in self.files:
            return [{"error": "not found"}]
        return [{"content": self.files[path]}]


def _configure(monkeypatch: pytest.MonkeyPatch, backend: _Backend) -> None:
    monkeypatch.setattr(
        iframe_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )

    async def get_backend(_thread_id: str) -> _Backend:
        return backend

    async def work_dir(_backend: _Backend) -> str:
        return "/workspace/project"

    monkeypatch.setattr(iframe_tool, "get_sandbox_backend", get_backend)
    monkeypatch.setattr(iframe_tool, "aresolve_sandbox_work_dir", work_dir)


async def test_output_iframe_returns_html_as_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _Backend(
        {
            "/workspace/project/chart.html": b"<html><head></head><body>Chart</body></html>",
            "/workspace/project/data.json": b'{"value":"</script><script>bad()</script>"}',
            "/workspace/project/theme.css": b"body { color: rebeccapurple; }",
        }
    )
    _configure(monkeypatch, backend)

    content, artifact = await iframe_tool._output_iframe(
        "chart.html",
        "Quarterly chart",
        {"data.json": "data.json", "theme.css": "theme.css"},
    )

    assert content == "Displayed the HTML output in the dashboard."
    assert artifact["type"] == "output_iframe"
    assert artifact["title"] == "Quarterly chart"
    assert artifact["filename"] == "chart.html"
    assert "window.__FILES__" in artifact["html"]
    assert "\\u003c/script>\\u003cscript>bad()" in artifact["html"]
    assert 'const name of ["theme.css"]' in artifact["html"]
    assert backend.downloaded == [
        "/workspace/project/chart.html",
        "/workspace/project/data.json",
        "/workspace/project/theme.css",
    ]


async def test_output_iframe_rejects_non_utf8_html(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _Backend({"/tmp/output.html": b"\xff"})
    _configure(monkeypatch, backend)

    with pytest.raises(ValueError, match="UTF-8"):
        await iframe_tool._output_iframe("/tmp/output.html")


async def test_output_iframe_rejects_oversized_html(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _Backend({"/tmp/output.html": b"x" * (iframe_tool._MAX_HTML_BYTES + 1)})
    _configure(monkeypatch, backend)

    with pytest.raises(ValueError, match="1 MB"):
        await iframe_tool._output_iframe("/tmp/output.html")


async def test_output_iframe_tool_keeps_artifact_out_of_model_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend({"/workspace/project/output.html": b"<h1>Output</h1>"})
    _configure(monkeypatch, backend)

    result = await iframe_tool.output_iframe.ainvoke(
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "output_iframe",
            "args": {"path": "output.html"},
        }
    )

    assert isinstance(result, ToolMessage)
    assert result.content == "Displayed the HTML output in the dashboard."
    assert result.artifact["html"] == "<h1>Output</h1>"
