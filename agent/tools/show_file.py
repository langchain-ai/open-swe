"""Tool: ``show_file``. Render a work-directory file as a rich card in the dashboard."""

import base64
import posixpath
import shlex
from typing import Any

from langchain_core.tools import tool

from agent.tools.create_sandbox_file_download_url import resolve_sandbox_file

MAX_TEXT_BYTES = 200_000
MAX_IMAGE_BYTES = 3_000_000

_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
_DIFF_SUFFIXES = {".patch", ".diff"}
_DIAGRAM_SUFFIXES = {".mmd", ".mermaid"}
_MARKDOWN_SUFFIXES = {".md", ".markdown"}


async def _show_file(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    title: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render a file from the working directory inline in the dashboard."""
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be 1 or greater")
    if end_line is not None and end_line < (start_line or 1):
        raise ValueError("end_line must not be before start_line")

    backend, source_path, work_dir = await resolve_sandbox_file(path)
    filename = posixpath.basename(source_path)
    relative_path = posixpath.relpath(source_path, work_dir)
    suffix = posixpath.splitext(filename)[1].lower()
    display_title = title.strip() if isinstance(title, str) and title.strip() else relative_path
    mime_type = _IMAGE_TYPES.get(suffix)

    size = await _file_size(backend, source_path)
    limit = MAX_IMAGE_BYTES if mime_type else MAX_TEXT_BYTES
    if size > limit:
        raise ValueError(
            f"{relative_path} is {size} bytes; the limit is {limit}. "
            "Write the relevant excerpt to a smaller file and show that instead."
        )

    (download,) = await backend.adownload_files([source_path])
    if download.error or download.content is None:
        raise ValueError(f"failed to read {relative_path}: {download.error or 'no content'}")
    data = download.content

    base = {
        "type": "show_file",
        "path": relative_path,
        "filename": filename,
        "title": display_title,
    }
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

    if suffix in _DIAGRAM_SUFFIXES:
        return (
            f"Displayed diagram {relative_path} in the dashboard.",
            {**base, "kind": "diagram", "content": text},
        )

    if suffix in _MARKDOWN_SUFFIXES:
        return (
            f"Displayed rendered markdown {relative_path} in the dashboard.",
            {**base, "kind": "markdown", "content": text},
        )

    if suffix in _DIFF_SUFFIXES or _looks_like_patch(text):
        return (
            f"Displayed diff {relative_path} in the dashboard.",
            {**base, "kind": "diff", "content": text},
        )

    total_lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if start_line is not None and start_line > max(total_lines, 1):
        raise ValueError(
            f"start_line {start_line} is past the end of {relative_path} ({total_lines} lines)"
        )
    first = start_line or 1
    last = min(end_line or total_lines, total_lines) if total_lines else 1
    shown = f" lines {first}-{last} of {total_lines}" if start_line or end_line else ""
    return (
        f"Displayed {relative_path}{shown} in the dashboard.",
        {
            **base,
            "kind": "text",
            "content": text,
            "total_lines": total_lines,
            "start_line": first,
            "end_line": last,
        },
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


show_file = tool(
    "show_file",
    description="""Render a file from the working directory as a rich card in the dashboard:
source code with syntax highlighting and line numbers, a unified diff (`.patch`/`.diff` or
`git diff` output) with per-file highlighting, a Mermaid diagram (`.mmd`/`.mermaid`), rendered
Markdown (`.md`), or an image. The user can select lines in code and diff cards and comment on
them, so this is the way to point at specific code.

Never retype diffs, file contents, logs, or generated output into a chat message from memory.
Write them to a file and show that instead, for example
`git diff > .open-swe/artifacts/changes.patch` followed by
`show_file(path=".open-swe/artifacts/changes.patch", title="Changes")`. To point at existing
source, pass its path with `start_line`/`end_line`; the full file is rendered with that range
highlighted. Keep temporary files under `.open-swe/artifacts/` and add that path to the
checkout's `.git/info/exclude`. Relative paths resolve from the working directory. Text files
are limited to 200 KB and images to 3 MB.""",
    response_format="content_and_artifact",
)(_show_file)
