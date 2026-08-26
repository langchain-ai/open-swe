import importlib
import shlex
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.utils.html_artifact import artifact_skeleton

iframe_tool = importlib.import_module("agent.tools.output_iframe")


class _Backend:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.stat_exit_code = 0
        self.stat_output = "42\n"
        self.copy_exit_code = 0
        self.copy_output = "42\n"

    async def aexecute(self, command: str, *, timeout: int | None = None) -> Any:
        self.commands.append(command)
        if command.startswith("test -f "):
            return SimpleNamespace(exit_code=self.stat_exit_code, output=self.stat_output)
        if command.startswith("mkdir -p "):
            return SimpleNamespace(exit_code=self.copy_exit_code, output=self.copy_output)
        return SimpleNamespace(exit_code=0, output="")


def _configure(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[str, dict[str, Any]]], _Backend]:
    calls: list[tuple[str, dict[str, Any]]] = []
    backend = _Backend()

    async def resolve_file(file_path: str) -> tuple[_Backend, str, str]:
        assert file_path == "chart.html"
        return backend, "/workspace/project/chart.html", "/workspace/project"

    async def create_download_url(file_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((file_path, kwargs))
        disposition = kwargs["content_disposition"]
        return {
            "url": f"https://downloads.example/{disposition}?token=secret",
            "file_path": file_path,
            "expires_at": None,
        }

    monkeypatch.setattr(iframe_tool, "_resolve_sandbox_file", resolve_file)
    monkeypatch.setattr(
        iframe_tool,
        "create_sandbox_file_download_url",
        create_download_url,
    )
    monkeypatch.setattr(iframe_tool, "uuid4", lambda: SimpleNamespace(hex="artifact-id"))
    return calls, backend


async def test_output_iframe_snapshots_html_and_returns_signed_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, backend = _configure(monkeypatch)
    snapshot_path = "/workspace/project/.open-swe/iframe-artifacts/artifact-id/chart.html"

    content, artifact = await iframe_tool._output_iframe("chart.html", "Quarterly chart")

    assert content == "Displayed the HTML output in the dashboard."
    assert artifact == {
        "type": "output_iframe",
        "preview_url": "https://downloads.example/inline?token=secret",
        "download_url": "https://downloads.example/attachment?token=secret",
        "title": "Quarterly chart",
        "filename": "chart.html",
    }
    prefix, suffix = artifact_skeleton("Quarterly chart")
    copy_source = "head -c 1000001 -- /workspace/project/chart.html"
    assert backend.commands == [
        "test -f /workspace/project/chart.html && stat -c %s -- /workspace/project/chart.html",
        "mkdir -p -- /workspace/project/.open-swe/iframe-artifacts/artifact-id && "
        f"{{ if grep -qi '<html[ >]' -- /workspace/project/chart.html; then {copy_source}; "
        f"else printf '%s' {shlex.quote(prefix)}; {copy_source}; "
        f"printf '%s' {shlex.quote(suffix)}; fi; }} > "
        "/workspace/project/.open-swe/iframe-artifacts/artifact-id/chart.html && "
        "stat -c %s -- "
        "/workspace/project/.open-swe/iframe-artifacts/artifact-id/chart.html",
    ]
    assert calls == [
        (
            snapshot_path,
            {
                "content_type": "text/html; charset=utf-8",
                "content_disposition": "inline",
            },
        ),
        (
            snapshot_path,
            {
                "content_type": "text/html; charset=utf-8",
                "content_disposition": "attachment",
            },
        ),
    ]


async def test_output_iframe_rejects_oversized_html_before_copying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, backend = _configure(monkeypatch)
    backend.stat_output = str(iframe_tool._MAX_HTML_BYTES + 1)

    with pytest.raises(ValueError, match="1 MB"):
        await iframe_tool._output_iframe("chart.html")

    assert len(backend.commands) == 1
    assert calls == []


async def test_output_iframe_rejects_snapshot_that_grows_during_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, backend = _configure(monkeypatch)
    prefix, suffix = artifact_skeleton(None)
    backend.copy_output = str(
        iframe_tool._MAX_HTML_BYTES + len(prefix.encode()) + len(suffix.encode()) + 1
    )

    with pytest.raises(ValueError, match="1 MB"):
        await iframe_tool._output_iframe("chart.html")

    assert backend.commands[-1] == (
        "rm -f -- /workspace/project/.open-swe/iframe-artifacts/artifact-id/chart.html"
    )
    assert calls == []


async def test_output_iframe_does_not_mint_urls_when_snapshot_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, backend = _configure(monkeypatch)
    backend.copy_exit_code = 1

    with pytest.raises(ValueError, match="failed to snapshot"):
        await iframe_tool._output_iframe("chart.html")

    assert calls == []


async def test_output_iframe_tool_keeps_urls_out_of_model_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    result = await iframe_tool.output_iframe.ainvoke(
        {
            "type": "tool_call",
            "id": "call-1",
            "name": "output_iframe",
            "args": {"path": "chart.html"},
        }
    )

    assert isinstance(result, ToolMessage)
    assert result.content == "Displayed the HTML output in the dashboard."
    assert result.artifact["preview_url"] == ("https://downloads.example/inline?token=secret")
    assert result.artifact["title"] == "HTML Output"
