"""Tool: ``save_plan``. Publish a sandbox HTML artifact for review or sharing."""

import logging
import re
from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from agent.sandboxes.state import get_sandbox_backend

from ..dashboard.plan_store import (
    PLAN_FILE_DIRECTORY,
    PLAN_STATUS_READY,
    PLAN_STATUS_SHARED,
    save_plan_content,
)
from ..utils.html_artifact import DEFAULT_TITLE, wrap_html_artifact

logger = logging.getLogger(__name__)

_MAX_PLAN_LINES = 20_000


async def save_plan(
    plan_file_path: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Publish a self-contained HTML plan artifact from the sandbox.

    Use this in plan mode once the artifact is ready. Outside plan mode, use it
    to share a long response without switching the thread into plan mode. Write
    one ``.html`` file directly under ``/workspace/plans/`` and pass that path
    here. Read the ``html-artifacts`` skill for the authoring rules: write the
    page content and omit ``<html>``/``<head>``/``<body>`` — they are added
    here, along with a minimal CSS reset — and include a ``<title>``. The
    artifact is rendered in an opaque-origin sandboxed iframe under a strict CSP:
    inline CSS and JavaScript, Canvas, WebGL, and Google Fonts work; network
    access and web storage do not.

    Args:
        plan_file_path: Path to the HTML artifact in the sandbox.

    Returns:
        ``{success: True, path}`` on success, or ``{success: False, error}``.
    """
    if not isinstance(plan_file_path, str):
        return {"success": False, "error": "plan_file_path must be a string"}
    path = plan_file_path.strip()
    if not path:
        return {"success": False, "error": "plan_file_path cannot be empty"}
    if not _is_html_path(path):
        return {
            "success": False,
            "error": f"plan_file_path must point to an HTML file in {PLAN_FILE_DIRECTORY}",
        }

    try:
        config = get_config()
    except Exception:
        config = {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not thread_id:
        return {"success": False, "error": "no thread_id in run config"}

    try:
        content = (await _read_plan_file(str(thread_id), path)).strip()
        if not content:
            return {"success": False, "error": "plan file cannot be empty"}
        document = wrap_html_artifact(content, title=_title_from_path(path))
        await _save(
            str(thread_id), document, path, plan_mode=_active_plan_mode(state, configurable)
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("save_plan failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to save plan: {exc}"}
    return {"success": True, "path": path}


async def _save(thread_id: str, content: str, path: str, *, plan_mode: bool) -> None:
    await save_plan_content(
        thread_id,
        html=content,
        status=PLAN_STATUS_READY if plan_mode else PLAN_STATUS_SHARED,
        plan_file_path=path,
        plan_mode=plan_mode or None,
    )


def _active_plan_mode(state: dict[str, Any] | None, configurable: Any) -> bool:
    if isinstance(state, dict) and state.get("plan_mode") is True:
        return True
    return isinstance(configurable, dict) and configurable.get("plan_mode") is True


async def _read_plan_file(thread_id: str, path: str) -> str:
    backend = await get_sandbox_backend(thread_id)
    result = await backend.aread(path, offset=0, limit=_MAX_PLAN_LINES)
    error = _value(result, "error")
    if error:
        raise ValueError(error)
    file_data = _value(result, "file_data")
    if file_data is None:
        raise ValueError("plan file could not be read")
    encoding = _value(file_data, "encoding")
    if encoding is not None and encoding != "utf-8":
        raise ValueError("plan file must be UTF-8 text")
    content = _value(file_data, "content")
    if not isinstance(content, str):
        raise ValueError("plan file content was not text")
    if content.count("\n") + 1 >= _MAX_PLAN_LINES:
        raise ValueError("plan file is too large")
    return content


def _value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _is_html_path(path: str) -> bool:
    if "\x00" in path or not path.startswith(f"{PLAN_FILE_DIRECTORY}/"):
        return False
    filename = path.removeprefix(f"{PLAN_FILE_DIRECTORY}/")
    return bool(filename and "/" not in filename and filename.lower().endswith(".html"))


def _title_from_path(path: str) -> str:
    """Fallback artifact name for a plan file that carries no title of its own."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".html")
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    words = stem.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else DEFAULT_TITLE
