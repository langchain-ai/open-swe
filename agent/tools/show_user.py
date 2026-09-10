"""Tool: ``show_user``. Render a work-directory file as a rich card in the dashboard."""

import base64
import posixpath
import shlex
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from agent.config import ENV
from agent.prompts import load_prompt
from agent.run_config import RunConfig
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.sandboxes.state import get_sandbox_backend
from agent.tools.create_sandbox_file_download_url import (
    create_sandbox_file_download_url,
    resolve_sandbox_file,
)
from agent.utils.html_artifact import artifact_skeleton, sandbox_wrap_command

MAX_TEXT_BYTES = 200_000
MAX_IMAGE_BYTES = 3_000_000
MAX_HTML_BYTES = 1_000_000
MAX_COMMAND_ERROR_CHARS = 4_000
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
ARTIFACT_DIR = ".open-swe/artifacts"

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
_KIND_BY_SUFFIX = {
    ".patch": "diff",
    ".diff": "diff",
    ".mmd": "diagram",
    ".mermaid": "diagram",
    ".md": "markdown",
    ".markdown": "markdown",
    ".html": "html",
    ".htm": "html",
}
_LIMITS = {"image": MAX_IMAGE_BYTES, "html": MAX_HTML_BYTES}


async def _show_user(
    path: str | None = None,
    command: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    title: str | None = None,
    timeout: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Implement the `show_user` tool."""
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be 1 or greater")
    if end_line is not None and end_line < (start_line or 1):
        raise ValueError("end_line must not be before start_line")
    if not (path or command):
        raise ValueError("pass path, command, or both")

    if command:
        path = await _run_command(command, path, timeout)

    backend, source_path, work_dir = await resolve_sandbox_file(str(path))
    filename = posixpath.basename(source_path)
    relative_path = posixpath.relpath(source_path, work_dir)
    suffix = posixpath.splitext(filename)[1].lower()
    fallback_title = command.strip() if command and command.strip() else relative_path
    display_title = title.strip() if isinstance(title, str) and title.strip() else fallback_title
    mime_type = _IMAGE_TYPES.get(suffix)
    kind = "image" if mime_type else _KIND_BY_SUFFIX.get(suffix, "text")

    limit = _LIMITS.get(kind, MAX_TEXT_BYTES)
    _enforce_limit(relative_path, await _file_size(backend, source_path), limit)

    base = {
        "type": "show_user",
        "path": relative_path,
        "filename": filename,
        "title": display_title,
    }
    if kind == "html":
        urls = await _html_preview_urls(backend, source_path, work_dir, filename, title)
        return (
            f"Displayed the HTML preview {relative_path} in the dashboard.",
            {**base, "kind": "html", **urls},
        )

    (download,) = await backend.adownload_files([source_path])
    if download.error or download.content is None:
        raise ValueError(f"failed to read {relative_path}: {download.error or 'no content'}")
    data = download.content
    # The file can grow between the stat above and this read, so the bytes we
    # actually got decide whether the artifact stays inside the cap.
    _enforce_limit(relative_path, len(data), limit)

    if mime_type:
        return (
            f"Displayed image {relative_path} in the dashboard.",
            {
                **base,
                "kind": "image",
                "mime_type": mime_type,
                "content_base64": base64.standard_b64encode(data).decode("ascii"),
            },
        )

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{relative_path} is not UTF-8 text or a supported image") from exc

    lines = text.splitlines()
    if kind == "diagram":
        return (
            _result(f"Displayed diagram {relative_path}", lines),
            {**base, "kind": "diagram", "content": text},
        )
    if kind == "markdown":
        return (
            _result(f"Displayed rendered markdown {relative_path}", lines),
            {**base, "kind": "markdown", "content": text},
        )
    if kind == "diff" or _looks_like_patch(text):
        return (
            _result(f"Displayed diff {relative_path}", lines),
            {**base, "kind": "diff", "content": text},
        )

    total_lines = len(lines)
    if start_line is not None and start_line > max(total_lines, 1):
        raise ValueError(
            f"start_line {start_line} is past the end of {relative_path} ({total_lines} lines)"
        )
    first = start_line or 1
    last = min(end_line or total_lines, total_lines) if total_lines else 1
    shown = f" lines {first}-{last} of {total_lines}" if start_line or end_line else ""
    return (
        _result(f"Displayed {relative_path}{shown}", lines[first - 1 : last]),
        {
            **base,
            "kind": "text",
            "content": text,
            "total_lines": total_lines,
            "start_line": first,
            "end_line": last,
        },
    )


async def _run_command(command: str, path: str | None, timeout: int | None) -> str:
    """Run `command`, capturing stdout to a kept file whose path is returned.

    Raises with both captured streams when the command exits non-zero, so the
    model sees the failure instead of a card built from partial output.
    """
    if not command.strip():
        raise ValueError("command cannot be empty")
    if timeout is not None and timeout < 1:
        raise ValueError("timeout must be positive")

    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")
    backend = await get_sandbox_backend(thread_id)
    work_dir = posixpath.normpath(await resolve_sandbox_work_dir(backend))

    if path:
        out_path = _resolve_output_path(path, work_dir)
    else:
        out_path = posixpath.join(work_dir, ARTIFACT_DIR, f"show-{uuid4().hex}.txt")
    err_path = f"{out_path}.stderr"

    quoted_out = shlex.quote(out_path)
    quoted_err = shlex.quote(err_path)
    result = await backend.aexecute(
        f"mkdir -p -- {shlex.quote(posixpath.dirname(out_path))} && "
        f"cd {shlex.quote(work_dir)} && "
        f"{{ {command}\n}} > {quoted_out} 2> {quoted_err}",
        timeout=timeout or DEFAULT_COMMAND_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        stdout = await _read_capture(backend, out_path)
        stderr = await _read_capture(backend, err_path)
        raise ValueError(
            f"command exited {result.exit_code}: {command}\n"
            f"--- stderr ({err_path}) ---\n{stderr or '(empty)'}\n"
            f"--- stdout ({out_path}) ---\n{stdout or '(empty)'}"
        )
    return out_path


def _resolve_output_path(path: str, work_dir: str) -> str:
    if not path.strip() or "\x00" in path:
        raise ValueError("path must be a non-empty sandbox path")
    candidate = path.strip()
    resolved = posixpath.normpath(
        candidate if candidate.startswith("/") else posixpath.join(work_dir, candidate)
    )
    if posixpath.commonpath((work_dir, resolved)) != work_dir:
        raise ValueError(f"path must resolve within the sandbox work directory ({work_dir})")
    return resolved


async def _read_capture(backend: Any, path: str) -> str:
    """Read a captured stream, trimmed to what is useful in a tool error."""
    result = await backend.aexecute(
        f"tail -c {MAX_COMMAND_ERROR_CHARS} -- {shlex.quote(path)} 2>/dev/null", timeout=10
    )
    return (result.output or "").strip() if result.exit_code == 0 else ""


async def _html_preview_urls(
    backend: Any, source_path: str, work_dir: str, filename: str, title: str | None
) -> dict[str, str]:
    """Snapshot an HTML file and mint signed inline/attachment URLs for it."""
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        raise ValueError("HTML previews are only available with the LangSmith sandbox")

    snapshot_dir = posixpath.join(work_dir, ".open-swe", "iframe-artifacts", uuid4().hex)
    snapshot_path = posixpath.join(snapshot_dir, filename)
    cleanup_command = f"rm -f -- {shlex.quote(snapshot_path)}"
    prefix, suffix = artifact_skeleton(title)
    copied = await backend.aexecute(
        f"mkdir -p -- {shlex.quote(snapshot_dir)} && "
        + sandbox_wrap_command(
            source_path,
            snapshot_path,
            limit=MAX_HTML_BYTES + 1,
            title=title,
        ),
        timeout=10,
    )
    if copied.exit_code != 0:
        await backend.aexecute(cleanup_command, timeout=10)
        raise ValueError("failed to snapshot the HTML file")
    try:
        snapshot_size = int(copied.output.strip())
    except (AttributeError, ValueError) as exc:
        await backend.aexecute(cleanup_command, timeout=10)
        raise ValueError("failed to determine the HTML snapshot size") from exc
    if snapshot_size > MAX_HTML_BYTES + len(prefix.encode()) + len(suffix.encode()):
        await backend.aexecute(cleanup_command, timeout=10)
        raise ValueError("HTML file exceeds the 1 MB limit")

    preview = await create_sandbox_file_download_url(
        snapshot_path,
        content_type="text/html; charset=utf-8",
        content_disposition="inline",
    )
    download = await create_sandbox_file_download_url(
        snapshot_path,
        content_type="text/html; charset=utf-8",
        content_disposition="attachment",
    )
    return {"preview_url": preview["url"], "download_url": download["url"]}


_EXCERPT_EDGE_LINES = 3
_EXCERPT_LINE_CHARS = 120


def _result(summary: str, lines: list[str]) -> str:
    """Tool result for the model: what was shown plus its first and last lines."""
    if not lines:
        return f"{summary} in the dashboard (empty)."
    clipped = [line[:_EXCERPT_LINE_CHARS] for line in lines]
    if len(clipped) > 2 * _EXCERPT_EDGE_LINES:
        omitted = len(clipped) - 2 * _EXCERPT_EDGE_LINES
        clipped = [
            *clipped[:_EXCERPT_EDGE_LINES],
            f"… {omitted} more lines …",
            *clipped[-_EXCERPT_EDGE_LINES:],
        ]
    excerpt = "\n".join(clipped)
    return f"{summary} in the dashboard. Refer to the card rather than repeating it.\n\n{excerpt}"


def _enforce_limit(relative_path: str, size: int, limit: int) -> None:
    if size > limit:
        raise ValueError(
            f"{relative_path} is {size} bytes; the limit is {limit}. "
            "Show a narrower excerpt instead."
        )


async def _file_size(backend: Any, path: str) -> int:
    quoted = shlex.quote(path)
    result = await backend.aexecute(f"test -f {quoted} && wc -c < {quoted}", timeout=10)
    if result.exit_code != 0:
        raise ValueError("path must identify a regular file")
    try:
        return int(result.output.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError("failed to determine the file size") from exc


def _looks_like_patch(text: str) -> bool:
    head = text.lstrip()[:2000].splitlines()
    return any(line.startswith("diff --git ") for line in head[:5]) or (
        len(head) >= 3
        and any(line.startswith("--- ") for line in head[:3])
        and any(line.startswith("+++ ") for line in head[:4])
    )


show_user = tool(
    "show_user",
    description=load_prompt("tools/show_user.md"),
    response_format="content_and_artifact",
)(_show_user)
