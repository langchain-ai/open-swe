import base64
import importlib
from types import SimpleNamespace
from typing import Any

import pytest

show_file_tool = importlib.import_module("agent.tools.show_file")


class _Backend:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    async def aexecute(self, command: str, *, timeout: int | None = None) -> Any:
        for path, data in self.files.items():
            if path in command:
                return SimpleNamespace(exit_code=0, output=f"{len(data)}\n")
        return SimpleNamespace(exit_code=1, output="")

    async def adownload_files(self, paths: list[str]) -> list[Any]:
        return [
            SimpleNamespace(path=path, content=self.files.get(path), error=None) for path in paths
        ]


def _configure(monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> _Backend:
    backend = _Backend(files)

    async def resolve_file(file_path: str) -> tuple[_Backend, str, str]:
        return backend, f"/workspace/project/{file_path}", "/workspace/project"

    monkeypatch.setattr(show_file_tool, "resolve_sandbox_file", resolve_file)
    return backend


@pytest.mark.asyncio
async def test_show_file_returns_full_text_with_requested_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {"/workspace/project/src/app.py": b"a\nb\nc\nd\n"})

    content, artifact = await show_file_tool._show_file("src/app.py", start_line=2, end_line=3)

    assert content == "Displayed src/app.py lines 2-3 of 4 in the dashboard."
    assert artifact == {
        "type": "show_file",
        "kind": "text",
        "path": "src/app.py",
        "filename": "app.py",
        "title": "src/app.py",
        "content": "a\nb\nc\nd\n",
        "total_lines": 4,
        "start_line": 2,
        "end_line": 3,
    }


@pytest.mark.asyncio
async def test_show_file_detects_patches_by_content(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
    _configure(monkeypatch, {"/workspace/project/.open-swe/artifacts/changes.txt": patch})

    content, artifact = await show_file_tool._show_file(
        ".open-swe/artifacts/changes.txt", title="Changes"
    )

    assert content == "Displayed diff .open-swe/artifacts/changes.txt in the dashboard."
    assert artifact["kind"] == "diff"
    assert artifact["title"] == "Changes"
    assert artifact["content"] == patch.decode()


@pytest.mark.asyncio
async def test_show_file_renders_mermaid_files_as_diagrams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {"/workspace/project/flow.mmd": b"graph TD\n  A --> B\n"})

    content, artifact = await show_file_tool._show_file("flow.mmd")

    assert content == "Displayed diagram flow.mmd in the dashboard."
    assert artifact["kind"] == "diagram"
    assert artifact["content"] == "graph TD\n  A --> B\n"


@pytest.mark.asyncio
async def test_show_file_renders_markdown_files(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {"/workspace/project/notes.md": b"# Title\n\ntext\n"})

    content, artifact = await show_file_tool._show_file("notes.md")

    assert content == "Displayed rendered markdown notes.md in the dashboard."
    assert artifact["kind"] == "markdown"
    assert artifact["content"] == "# Title\n\ntext\n"


@pytest.mark.asyncio
async def test_show_file_encodes_images(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"\x89PNG\r\n\x1a\n"
    _configure(monkeypatch, {"/workspace/project/shot.png": data})

    _, artifact = await show_file_tool._show_file("shot.png")

    assert artifact["kind"] == "image"
    assert artifact["mime_type"] == "image/png"
    assert artifact["content_base64"] == base64.standard_b64encode(data).decode()


@pytest.mark.asyncio
async def test_show_file_rejects_oversized_files_before_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _configure(monkeypatch, {"/workspace/project/big.log": b"x" * 10})
    backend.files["/workspace/project/big.log"] = b"x" * (show_file_tool.MAX_TEXT_BYTES + 1)

    async def fail_download(paths: list[str]) -> list[Any]:
        raise AssertionError("must not download oversized files")

    monkeypatch.setattr(backend, "adownload_files", fail_download)
    with pytest.raises(ValueError, match="limit"):
        await show_file_tool._show_file("big.log")


@pytest.mark.asyncio
async def test_show_file_rejects_binary_non_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {"/workspace/project/blob.bin": b"\xff\xfe\x00\x01"})

    with pytest.raises(ValueError, match="not UTF-8"):
        await show_file_tool._show_file("blob.bin")


@pytest.mark.asyncio
async def test_show_file_rejects_range_past_end(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {"/workspace/project/a.txt": b"one\n"})

    with pytest.raises(ValueError, match="past the end"):
        await show_file_tool._show_file("a.txt", start_line=5)
