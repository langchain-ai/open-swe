"""Tool: ``save_plan``. Record the implementation plan for review.

Writes the plan as a real ``plan.md`` file in the sandbox (the artifact the
agent produces and can re-read) and publishes it to the plan-review page, where
the user and collaborators read it, comment inline, and approve or request
changes. Available in plan mode (it does not modify the repository under review).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from ..dashboard.plan_store import PLAN_STATUS_READY, save_plan_content

logger = logging.getLogger(__name__)

PLAN_FILE_PATH = "plan.md"


def save_plan(plan_markdown: str) -> dict[str, Any]:
    """Write your implementation plan as a markdown file and publish it for review.

    Use this in plan mode once your plan is ready. The plan is saved as
    ``plan.md`` in the sandbox and published to the plan-review page linked in
    the conversation, where the user (the owner) and any collaborators can read
    it, leave inline comments, and then approve it or request changes. Call it
    again to overwrite the plan with a revised version when addressing feedback.

    Write the plan in standard Markdown — headings, bullet/numbered lists, and
    fenced code blocks all render. Structure it clearly (overview, files to
    change, ordered steps, risks).

    Args:
        plan_markdown: The full plan, as a Markdown document.

    Returns:
        ``{success: True, path}`` on success, or ``{success: False, error}``.
    """
    content = plan_markdown.strip()
    if not content:
        return {"success": False, "error": "plan_markdown cannot be empty"}

    try:
        config = get_config()
    except Exception:
        config = {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not thread_id:
        return {"success": False, "error": "no thread_id in run config"}

    try:
        path = asyncio.run(_save(str(thread_id), content))
    except Exception as exc:  # noqa: BLE001
        logger.exception("save_plan failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to save plan: {exc}"}
    return {"success": True, "path": path}


async def _save(thread_id: str, content: str) -> str:
    sandbox_path = await _write_to_sandbox(thread_id, content)
    await save_plan_content(thread_id, markdown=content, status=PLAN_STATUS_READY)
    return sandbox_path


async def _write_to_sandbox(thread_id: str, content: str) -> str:
    """Write ``plan.md`` into the thread's sandbox. Best-effort: a missing sandbox
    must not block publishing the plan to the review page."""
    try:
        from ..utils.sandbox_state import get_sandbox_backend

        backend = await get_sandbox_backend(thread_id)
        await backend.awrite(PLAN_FILE_PATH, content)
        return PLAN_FILE_PATH
    except Exception:
        logger.warning("Could not write plan.md to sandbox for %s", thread_id, exc_info=True)
        return PLAN_FILE_PATH
