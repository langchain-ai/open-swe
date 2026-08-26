import posixpath
import shlex
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from ..utils.html_artifact import artifact_skeleton
from .create_sandbox_file_download_url import (
    _resolve_sandbox_file,
    create_sandbox_file_download_url,
)

_MAX_HTML_BYTES = 1_000_000


async def _output_iframe(
    path: str,
    title: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Display a sandbox HTML file in an isolated iframe in the dashboard."""
    backend, source_path, work_dir = await _resolve_sandbox_file(path)
    quoted_source = shlex.quote(source_path)
    stat = await backend.aexecute(f"test -f {quoted_source} && stat -c %s -- {quoted_source}")
    if stat.exit_code != 0:
        raise ValueError("HTML path must identify a regular file")
    try:
        size = int(stat.output.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError("failed to determine the HTML file size") from exc
    if size > _MAX_HTML_BYTES:
        raise ValueError("HTML file exceeds the 1 MB limit")

    filename = posixpath.basename(source_path) or "output.html"
    snapshot_dir = posixpath.join(
        work_dir,
        ".open-swe",
        "iframe-artifacts",
        uuid4().hex,
    )
    snapshot_path = posixpath.join(snapshot_dir, filename)
    quoted_snapshot = shlex.quote(snapshot_path)
    cleanup_command = f"rm -f -- {quoted_snapshot}"
    prefix, suffix = artifact_skeleton(title)
    copy_source = f"head -c {_MAX_HTML_BYTES + 1} -- {quoted_source}"
    copied = await backend.aexecute(
        f"mkdir -p -- {shlex.quote(snapshot_dir)} && "
        f"{{ if grep -qi '<html[ >]' -- {quoted_source}; then {copy_source}; else "
        f"printf '%s' {shlex.quote(prefix)}; {copy_source}; "
        f"printf '%s' {shlex.quote(suffix)}; fi; }} > {quoted_snapshot} && "
        f"stat -c %s -- {quoted_snapshot}",
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
    if snapshot_size > _MAX_HTML_BYTES + len(prefix.encode()) + len(suffix.encode()):
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
    display_title = title.strip() if isinstance(title, str) and title.strip() else "HTML Output"
    return (
        "Displayed the HTML output in the dashboard.",
        {
            "type": "output_iframe",
            "preview_url": preview["url"],
            "download_url": download["url"],
            "title": display_title,
            "filename": filename,
        },
    )


output_iframe = tool(
    "output_iframe",
    description="""Display a self-contained HTML file from the sandbox in an isolated dashboard
iframe. Use this for visualizations, diagrams, interactive demos, SVG graphics, and small HTML
apps. Read the `html-artifacts` skill for the authoring rules: inline scripts, styles, Canvas,
WebGL, and data-URI assets all run, and omitting `<html>`/`<head>`/`<body>` wraps the content
in that skeleton with a minimal CSS reset. Relative paths are resolved from the sandbox
working directory. Do not use this for regular text responses or file operations.""",
    response_format="content_and_artifact",
)(_output_iframe)
