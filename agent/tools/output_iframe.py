import json
import posixpath
import re
from collections.abc import Mapping
from typing import Any

from langchain_core.tools import tool
from langgraph.config import get_config

from ..utils.sandbox_paths import aresolve_sandbox_work_dir
from ..utils.sandbox_state import get_sandbox_backend

_MAX_HTML_BYTES = 1_000_000
_MAX_TOTAL_BYTES = 2_000_000
_MAX_BUNDLED_FILES = 20


async def _output_iframe(
    path: str,
    title: str | None = None,
    files: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Display a sandbox HTML file in an isolated iframe in the dashboard."""
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("no thread_id in run config")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path is required")

    backend = await get_sandbox_backend(thread_id)
    work_dir = await aresolve_sandbox_work_dir(backend)
    html_path = _resolve_path(work_dir, path.strip())
    html_bytes = await _download_file(backend, html_path)
    if len(html_bytes) > _MAX_HTML_BYTES:
        raise ValueError("HTML file exceeds the 1 MB limit")
    html = _decode_utf8(html_bytes, html_path)

    bundled = files or {}
    if len(bundled) > _MAX_BUNDLED_FILES:
        raise ValueError(f"files cannot contain more than {_MAX_BUNDLED_FILES} entries")

    total_bytes = len(html_bytes)
    file_contents: dict[str, str] = {}
    for name, file_path in bundled.items():
        _validate_file_entry(name, file_path)
        resolved_path = _resolve_path(work_dir, file_path)
        content = await _download_file(backend, resolved_path)
        total_bytes += len(content)
        if total_bytes > _MAX_TOTAL_BYTES:
            raise ValueError("HTML and bundled files exceed the 2 MB limit")
        file_contents[name] = _decode_utf8(content, resolved_path)

    rendered_html = _inject_files(html, file_contents)
    filename = posixpath.basename(html_path) or "output.html"
    display_title = title.strip() if isinstance(title, str) and title.strip() else "HTML Output"
    return (
        "Displayed the HTML output in the dashboard.",
        {
            "type": "output_iframe",
            "html": rendered_html,
            "title": display_title,
            "filename": filename,
        },
    )


output_iframe = tool(
    "output_iframe",
    description="""Display an HTML file from the sandbox in an isolated dashboard iframe.

Use this for visualizations, diagrams, interactive demos, SVG graphics, and small HTML apps.
The HTML may contain inline scripts and styles. Pass small supporting JSON, CSV, CSS, JavaScript,
or text files with `files`; their contents become strings in `window.__FILES__`, and CSS files are
also applied to the page. Relative paths are resolved from the sandbox working directory. Do not
use this for regular text responses or file operations.""",
    response_format="content_and_artifact",
)(_output_iframe)


def _resolve_path(work_dir: str, path: str) -> str:
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(work_dir, path))


async def _download_file(backend: Any, path: str) -> bytes:
    responses = await backend.adownload_files([path])
    response = responses[0] if responses else None
    error = _value(response, "error")
    if error:
        raise ValueError(f"failed to read {path}: {error}")
    content = _value(response, "content")
    if isinstance(content, str):
        return content.encode()
    if isinstance(content, bytes):
        return content
    raise ValueError(f"failed to read {path}")


def _decode_utf8(content: bytes, path: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} must be UTF-8 text") from exc


def _validate_file_entry(name: str, path: str) -> None:
    if not isinstance(name, str) or not name or len(name) > 200 or "\x00" in name:
        raise ValueError("file names must be non-empty strings of at most 200 characters")
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError(f"file path for {name!r} must be a non-empty string")


def _inject_files(html: str, files: dict[str, str]) -> str:
    if not files:
        return html
    serialized_files = _serialize_for_script(files)
    serialized_css_names = _serialize_for_script(
        [name for name in files if name.lower().endswith(".css")]
    )
    injection = (
        '<script data-output-iframe-files="true">\n'
        f"window.__FILES__ = {serialized_files};\n"
        f"for (const name of {serialized_css_names}) {{\n"
        "  const style = document.createElement('style');\n"
        "  style.dataset.file = name;\n"
        "  style.textContent = window.__FILES__[name];\n"
        "  (document.head || document.documentElement).appendChild(style);\n"
        "}\n"
        "</script>\n"
    )
    head = re.search(r"<head\b[^>]*>", html, flags=re.IGNORECASE)
    if head:
        return html[: head.end()] + "\n" + injection + html[head.end() :]
    return injection + html


def _serialize_for_script(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")


def _value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)
