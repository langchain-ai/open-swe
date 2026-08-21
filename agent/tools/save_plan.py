"""Tool: ``save_plan``. Publish a sandbox HTML artifact for review or sharing."""

import logging
from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from ..sandboxes.registry import get_sandbox_backend
from ..settings.plan_store import (
    PLAN_FILE_DIRECTORY,
    PLAN_STATUS_READY,
    PLAN_STATUS_SHARED,
    save_plan_content,
)

logger = logging.getLogger(__name__)

_MAX_PLAN_LINES = 20_000


async def save_plan(
    plan_file_path: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Publish a self-contained HTML plan artifact from the sandbox.

    Use this in plan mode once the artifact is ready. Outside plan mode, use it
    to share a long response without switching the thread into plan mode. Create
    one complete ``.html`` document directly under ``/workspace/plans/`` and pass
    that path here. The document is displayed only in a sandboxed iframe.

    The HTML must include a descriptive ``<title>``, real content, inline CSS,
    explicit body background and text colors, accessible focus states, and a
    responsive layout. Inline or data-URI every asset. Google Fonts stylesheets
    are the only permitted external resources; include fallback font stacks.
    Define complete light tokens in ``:root``, system-dark overrides in
    ``@media (prefers-color-scheme: dark)`` guarded by
    ``:root:not([data-theme=\"light\"])``, and explicit dark overrides in
    ``:root[data-theme=\"dark\"]``. Do not include scripts, forms, embeds, or
    dependencies on host-page CSS or JavaScript.

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
        validation_error = _validate_html(content)
        if validation_error:
            return {"success": False, "error": validation_error}
        await _save(str(thread_id), content, path, plan_mode=_active_plan_mode(state, configurable))
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


def _validate_html(content: str) -> str | None:
    lowered = content.lower()
    if "<html" not in lowered or "<head" not in lowered or "<body" not in lowered:
        return "plan file must be a complete HTML document with html, head, and body elements"
    if "<title" not in lowered:
        return "plan file must include a descriptive title"
    if "<script" in lowered:
        return "plan HTML cannot include scripts"
    if "<iframe" in lowered or "<object" in lowered or "<embed" in lowered:
        return "plan HTML cannot include nested embeds"
    if "<form" in lowered:
        return "plan HTML cannot include forms"
    return None
