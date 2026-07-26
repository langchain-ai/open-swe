"""Tool: ``save_plan``. Publish sandbox Markdown for review or sharing.

Reads the Markdown file the agent created in the sandbox and publishes it to the
plan-review page. In plan mode it is an approvable implementation plan; outside
plan mode it is read-only shared content.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Annotated, Any

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from ..dashboard.plan_store import (
    PLAN_FILE_DIRECTORY,
    PLAN_STATUS_READY,
    PLAN_STATUS_SHARED,
    save_plan_content,
)
from ..utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)

_MAX_PLAN_LINES = 20_000
_MARKDOWN_EXTENSIONS = (".md", ".markdown")


async def save_plan(
    plan_file_path: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> dict[str, Any]:
    """Publish a Markdown plan file from the sandbox for review.

    Use this in plan mode once your plan is ready. Outside plan mode, use it to
    share long Slack responses without switching the thread into plan mode. First
    create a Markdown file under ``/workspace/plans/`` using a dated, descriptive
    filename, then pass that file path here. The file contents are published to
    the plan-review page linked in the conversation. In plan mode, the user can
    comment, approve, or request changes; outside plan mode, the page is read-only
    shared content.

    Write the content in standard Markdown — headings, bullet/numbered lists, and
    fenced code blocks all render. Shared responses persist Markdown text only;
    they do not upload or serve sandbox-local or relative image files. If images
    or screenshots are needed for a Slack response, post them directly in Slack
    instead of relying on the shared response page.

    Args:
        plan_file_path: Path to the Markdown plan file in the sandbox.

    Returns:
        ``{success: True, path}`` on success, or ``{success: False, error}``.
    """
    if not isinstance(plan_file_path, str):
        return {"success": False, "error": "plan_file_path must be a string"}
    path = plan_file_path.strip()
    if not path:
        return {"success": False, "error": "plan_file_path cannot be empty"}
    if not _is_markdown_path(path):
        return {
            "success": False,
            "error": f"plan_file_path must point to a Markdown file in {PLAN_FILE_DIRECTORY}",
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
        await _save(str(thread_id), content, path, plan_mode=_active_plan_mode(state, configurable))
    except Exception as exc:  # noqa: BLE001
        logger.exception("save_plan failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to save plan: {exc}"}
    return {"success": True, "path": path}


async def _save(thread_id: str, content: str, path: str, *, plan_mode: bool) -> None:
    await save_plan_content(
        thread_id,
        markdown=content,
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


def _is_markdown_path(path: str) -> bool:
    if "\x00" in path or not path.startswith(f"{PLAN_FILE_DIRECTORY}/"):
        return False
    filename = path.removeprefix(f"{PLAN_FILE_DIRECTORY}/")
    if not filename or "/" in filename:
        return False
    return filename.lower().endswith(_MARKDOWN_EXTENSIONS)
