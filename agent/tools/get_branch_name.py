"""Tool to get the branch name for the current thread."""
from __future__ import annotations

from typing import Any

from langgraph.config import get_config


def get_branch_name() -> dict[str, Any]:
    """Return the git branch name for this thread (open-swe/{thread_id})."""
    config = get_config()
    thread_id = config.get("configurable", {}).get("thread_id", "unknown")
    return {"branch_name": f"open-swe/{thread_id}"}
