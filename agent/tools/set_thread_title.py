from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_config
from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

MAX_THREAD_TITLE_CHARS = 80


async def set_thread_title(title: str) -> dict[str, Any]:
    """Set a concise title for the current thread early after understanding the task."""
    clean_title = title.strip() if isinstance(title, str) else ""
    if not clean_title:
        return {"success": False, "error": "title is required"}
    if len(clean_title) > MAX_THREAD_TITLE_CHARS:
        return {
            "success": False,
            "error": f"title exceeds the {MAX_THREAD_TITLE_CHARS} character limit",
        }

    configurable = get_config().get("configurable", {})
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id.strip():
        return {"success": False, "error": "Missing configurable.thread_id in config"}

    try:
        await get_client().threads.update(
            thread_id=thread_id.strip(),
            metadata={"title": clean_title},
        )
    except Exception:
        logger.exception("Failed to update title for thread %s", thread_id)
        return {"success": False, "error": "Could not update thread title"}

    return {"success": True, "title": clean_title}
