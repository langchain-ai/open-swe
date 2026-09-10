import base64
import importlib
import posixpath
import shlex
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.utils.html_artifact import artifact_skeleton, sandbox_wrap_command

show_user_tool = importlib.import_module("agent.tools.show_user")

WORK_DIR = "/workspace/project"


class _Backend:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.commands: list[str] = []
        self.timeouts: list[int | None] = []
        self.copy_exit_code = 0
        self.copy_output = "42\n"
        self.run_exit_code = 0
        self.captures: dict[str, str] = {}

    async def aexecute(self, command: str, *, timeout: int | None = None) -> Any:
        self.commands.append(command)
        self.timeouts.append(timeout)
        if "} > " in command:
            return SimpleNamespace(exit_code=self.run_exit_code, output="")
        if command.startswith("tail -c "):
            for path, text in self.captures.items():
                if f"-- {shlex.quote(path)} " in command:
                    return SimpleNamespace(exit_code=0, output=text)
            return SimpleNamespace(exit_code=0, output="")
        if command.startswith("mkdir -p "):
            return SimpleNamespace(exit_code=self.copy_exit_code, output=self.copy_output)
        if command.startswith("test -f "):
            for path, data in self.files.items():
                if path in command:
                    return SimpleNamespace(exit_code=0, output=f"{len(data)}\n")
            return SimpleNamespace(exit_code=1, output="")
        return SimpleNamespace(exit_code=0, output="")

    async def adownload_files(self, paths: list[str]) -> list[Any]:
        return [
            SimpleNamespace(path=path, content=self.files.get(path), error=None) for path in paths
        ]


def _configure(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]
) -> tuple[_Backend, list[tuple[str, dict[str, Any]]]]:
    backend = _Backend(files)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def resolve_file(file_path: str) -> tuple[_Backend, str, str]:
        if file_path.startswith("/"):
            return backend, file_path, WORK_DIR
        return backend, f"{WORK_DIR}/{file_path}", WORK_DIR

    async def sandbox_backend(thread_id: str) -> _Backend:
        del thread_id
        return backend

    async def work_dir(_: Any) -> str:
        return WORK_DIR

    monkeypatch.setattr(
        show_user_tool.RunConfig, "from_runtime", classmethod(lambda cls: cls(thread_id="t1"))
    )
    monkeypatch.setattr(show_user_tool, "get_sandbox_backend", sandbox_backend)
    monkeypatch.setattr(show_user_tool, "resolve_sandbox_work_dir", work_dir)

    async def create_download_url(file_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((file_path, kwargs))
        disposition = kwargs["content_disposition"]
        return {
            "url": f"https://downloads.example/{disposition}?token=secret",
            "file_path": file_path,
            "expires_at": None,
        }

    monkeypatch.setattr(show_user_tool, "resolve_sandbox_file", resolve_file)
    monkeypatch.setattr(show_user_tool, "create_sandbox_file_download_url", create_download_url)
    monkeypatch.setattr(show_user_tool, "uuid4", lambda: SimpleNamespace(hex="artifact-id"))
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    return backend, calls


@pytest.mark.asyncio
async def test_show_user_returns_full_text_with_requested_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {f"{WORK_DIR}/src/app.py": b"a\nb\nc\nd\n"})

    content, artifact = await show_user_tool._show_user("src/app.py", start_line=2, end_line=3)

    assert content == (
        "Displayed src/app.py lines 2-3 of 4 in the dashboard. "
        "Refer to the card rather than repeating it.\n\nb\nc"
    )
    assert artifact == {
        "type": "show_user",
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
async def test_show_user_result_elides_the_middle_of_long_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "\n".join(f"line {index}" for index in range(1, 21)) + "\n"
    _configure(monkeypatch, {f"{WORK_DIR}/big.txt": body.encode()})

    content, _ = await show_user_tool._show_user("big.txt")

    assert content.endswith("line 1\nline 2\nline 3\n… 14 more lines …\nline 18\nline 19\nline 20")


@pytest.mark.asyncio
async def test_show_user_runs_a_command_and_renders_its_captured_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_path = f"{WORK_DIR}/{show_user_tool.ARTIFACT_DIR}/show-artifact-id.txt"
    backend, _ = _configure(monkeypatch, {out_path: b"one\ntwo\n"})

    content, artifact = await show_user_tool._show_user(command="git diff")

    run = next(cmd for cmd in backend.commands if "} > " in cmd)
    assert f"mkdir -p -- {shlex.quote(posixpath.dirname(out_path))}" in run
    assert f"cd {shlex.quote(WORK_DIR)}" in run
    assert (
        f"{{ git diff\n}} > {shlex.quote(out_path)} 2> {shlex.quote(out_path + '.stderr')}" in run
    )
    assert backend.timeouts[0] == show_user_tool.DEFAULT_COMMAND_TIMEOUT_SECONDS
    assert artifact["kind"] == "text"
    assert artifact["content"] == "one\ntwo\n"
    # No explicit title, so the command names the card.
    assert artifact["title"] == "git diff"
    assert content.startswith("Displayed")


@pytest.mark.asyncio
async def test_show_user_command_writes_to_the_requested_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_path = f"{WORK_DIR}/.open-swe/artifacts/flow.mmd"
    backend, _ = _configure(monkeypatch, {out_path: b"graph TD\n  A --> B\n"})

    _, artifact = await show_user_tool._show_user(
        command="./gen", path=".open-swe/artifacts/flow.mmd"
    )

    assert any(shlex.quote(out_path) in cmd for cmd in backend.commands if "} > " in cmd)
    assert artifact["kind"] == "diagram"


@pytest.mark.asyncio
async def test_show_user_failed_command_raises_with_both_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_path = f"{WORK_DIR}/{show_user_tool.ARTIFACT_DIR}/show-artifact-id.txt"
    backend, _ = _configure(monkeypatch, {})
    backend.run_exit_code = 2
    backend.captures = {out_path: "partial output", f"{out_path}.stderr": "boom: no such ref"}

    with pytest.raises(ValueError) as excinfo:
        await show_user_tool._show_user(command="git diff bogus")

    message = str(excinfo.value)
    assert "command exited 2: git diff bogus" in message
    assert "boom: no such ref" in message
    assert "partial output" in message


@pytest.mark.asyncio
async def test_show_user_failed_command_reports_empty_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = _configure(monkeypatch, {})
    backend.run_exit_code = 1

    with pytest.raises(ValueError, match=r"\(empty\)"):
        await show_user_tool._show_user(command="false")


@pytest.mark.asyncio
async def test_show_user_command_output_path_stays_in_the_work_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = _configure(monkeypatch, {})

    with pytest.raises(ValueError, match="work directory"):
        await show_user_tool._show_user(command="echo hi", path="../escape.txt")

    assert backend.commands == []


@pytest.mark.asyncio
async def test_show_user_requires_path_or_command(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {})

    with pytest.raises(ValueError, match="pass path, command, or both"):
        await show_user_tool._show_user()


@pytest.mark.asyncio
async def test_show_user_rejects_an_empty_command(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {})

    with pytest.raises(ValueError, match="command cannot be empty"):
        await show_user_tool._show_user(command="   ")


@pytest.mark.asyncio
async def test_show_user_detects_patches_by_content(monkeypatch: pytest.MonkeyPatch) -> None:
    patch = b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
    _configure(monkeypatch, {f"{WORK_DIR}/.open-swe/artifacts/changes.txt": patch})

    content, artifact = await show_user_tool._show_user(
        ".open-swe/artifacts/changes.txt", title="Changes"
    )

    assert content.startswith("Displayed diff .open-swe/artifacts/changes.txt in the dashboard.")
    assert content.endswith("diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new")
    assert artifact["kind"] == "diff"
    assert artifact["title"] == "Changes"
    assert artifact["content"] == patch.decode()


@pytest.mark.asyncio
async def test_show_user_renders_mermaid_files_as_diagrams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {f"{WORK_DIR}/flow.mmd": b"graph TD\n  A --> B\n"})

    content, artifact = await show_user_tool._show_user("flow.mmd")

    assert content.startswith("Displayed diagram flow.mmd in the dashboard.")
    assert artifact["kind"] == "diagram"
    assert artifact["content"] == "graph TD\n  A --> B\n"


@pytest.mark.asyncio
async def test_show_user_renders_markdown_files(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {f"{WORK_DIR}/notes.md": b"# Title\n\ntext\n"})

    content, artifact = await show_user_tool._show_user("notes.md")

    assert content.startswith("Displayed rendered markdown notes.md in the dashboard.")
    assert artifact["kind"] == "markdown"
    assert artifact["content"] == "# Title\n\ntext\n"


@pytest.mark.asyncio
async def test_show_user_encodes_images(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"\x89PNG\r\n\x1a\n"
    _configure(monkeypatch, {f"{WORK_DIR}/shot.png": data})

    _, artifact = await show_user_tool._show_user("shot.png")

    assert artifact["kind"] == "image"
    assert artifact["mime_type"] == "image/png"
    assert artifact["content_base64"] == base64.standard_b64encode(data).decode()


@pytest.mark.asyncio
async def test_show_user_snapshots_html_and_returns_signed_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, calls = _configure(monkeypatch, {f"{WORK_DIR}/chart.html": b"<h1>hi</h1>"})
    snapshot_path = f"{WORK_DIR}/.open-swe/iframe-artifacts/artifact-id/chart.html"

    content, artifact = await show_user_tool._show_user("chart.html", title="Quarterly chart")

    assert content == "Displayed the HTML preview chart.html in the dashboard."
    assert artifact == {
        "type": "show_user",
        "kind": "html",
        "path": "chart.html",
        "filename": "chart.html",
        "title": "Quarterly chart",
        "preview_url": "https://downloads.example/inline?token=secret",
        "download_url": "https://downloads.example/attachment?token=secret",
    }
    assert backend.commands == [
        f"test -f {WORK_DIR}/chart.html && wc -c < {WORK_DIR}/chart.html",
        f"mkdir -p -- {WORK_DIR}/.open-swe/iframe-artifacts/artifact-id && "
        + sandbox_wrap_command(
            f"{WORK_DIR}/chart.html",
            snapshot_path,
            limit=show_user_tool.MAX_HTML_BYTES + 1,
            title="Quarterly chart",
        ),
    ]
    assert [call[1]["content_disposition"] for call in calls] == ["inline", "attachment"]
    assert all(call[0] == snapshot_path for call in calls)


@pytest.mark.asyncio
async def test_show_user_rejects_html_snapshot_that_grows_during_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, calls = _configure(monkeypatch, {f"{WORK_DIR}/chart.html": b"<h1>hi</h1>"})
    prefix, suffix = artifact_skeleton(None)
    backend.copy_output = str(
        show_user_tool.MAX_HTML_BYTES + len(prefix.encode()) + len(suffix.encode()) + 1
    )

    with pytest.raises(ValueError, match="1 MB"):
        await show_user_tool._show_user("chart.html")

    assert backend.commands[-1] == (
        f"rm -f -- {WORK_DIR}/.open-swe/iframe-artifacts/artifact-id/chart.html"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_show_user_html_requires_langsmith_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, calls = _configure(monkeypatch, {f"{WORK_DIR}/chart.html": b"<h1>hi</h1>"})
    monkeypatch.setenv("SANDBOX_TYPE", "modal")

    with pytest.raises(ValueError, match="LangSmith sandbox"):
        await show_user_tool._show_user("chart.html")

    assert len(backend.commands) == 1
    assert calls == []


@pytest.mark.asyncio
async def test_show_user_tool_keeps_urls_out_of_model_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, {f"{WORK_DIR}/chart.html": b"<h1>hi</h1>"})

    result = await show_user_tool.show_user.ainvoke(
        {
            "type": "tool_call",
            "name": "show_user",
            "args": {"path": "chart.html"},
            "id": "call-1",
        }
    )

    assert isinstance(result, ToolMessage)
    assert "downloads.example" not in str(result.content)
    assert result.artifact["preview_url"] == "https://downloads.example/inline?token=secret"


@pytest.mark.asyncio
async def test_show_user_rejects_oversized_files_before_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _ = _configure(
        monkeypatch, {f"{WORK_DIR}/big.log": b"x" * (show_user_tool.MAX_TEXT_BYTES + 1)}
    )

    async def fail_download(paths: list[str]) -> list[Any]:
        raise AssertionError("must not download oversized files")

    monkeypatch.setattr(backend, "adownload_files", fail_download)
    with pytest.raises(ValueError, match="limit"):
        await show_user_tool._show_user("big.log")


@pytest.mark.asyncio
async def test_show_user_rejects_binary_non_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {f"{WORK_DIR}/blob.bin": b"\xff\xfe\x00\x01"})

    with pytest.raises(ValueError, match="not UTF-8"):
        await show_user_tool._show_user("blob.bin")


@pytest.mark.asyncio
async def test_show_user_rejects_range_past_end(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, {f"{WORK_DIR}/a.txt": b"one\n"})

    with pytest.raises(ValueError, match="past the end"):
        await show_user_tool._show_user("a.txt", start_line=5)
