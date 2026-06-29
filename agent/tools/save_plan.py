"""Tool: ``save_plan``. Publish the sandbox plan file for review.

Reads the Markdown plan file the agent created in the sandbox and publishes it to
the plan-review page, where the user and collaborators read it, comment inline,
and approve or request changes. Available in plan mode (it does not modify the
repository under review).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config

from ..dashboard.plan_store import PLAN_STATUS_READY, save_plan_content
from ..utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)

_MAX_PLAN_LINES = 20_000
_MARKDOWN_EXTENSIONS = (".md", ".markdown")


async def save_plan(plan_file_path: str) -> dict[str, Any]:
    """Publish a Markdown plan file from the sandbox for review.

    Use this in plan mode once your plan is ready. First create a Markdown file
    outside any cloned repository (for example, ``/workspace/plan.md``) with the
    normal file-editing tools, then pass that file path here. The file contents
    are published to the plan-review page linked in the conversation, where the
    user (the owner) and any collaborators can read it, leave inline comments,
    and then approve it or request changes. Call it again to publish a revised
    file when addressing feedback.

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
        return {"success": False, "error": "plan_file_path must point to a Markdown file"}

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
        await _save(str(thread_id), content)
    except Exception as exc:  # noqa: BLE001
        logger.exception("save_plan failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to save plan: {exc}"}
    return {"success": True, "path": path}


async def _save(thread_id: str, content: str) -> None:
    await save_plan_content(thread_id, markdown=content, status=PLAN_STATUS_READY)


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
    lowered = path.lower()
    return lowered.endswith(_MARKDOWN_EXTENSIONS) and "\x00" not in lowered
