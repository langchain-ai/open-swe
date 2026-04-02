from typing import Any

from langgraph.config import get_config


def get_branch_name() -> dict[str, Any]:
    """Return the git branch name for this thread.

    Returns the branch_name from thread metadata if set,
    otherwise falls back to open-swe/{thread_id}.
    """
    config = get_config()
    metadata = config.get("metadata", {})
    branch_name = metadata.get("branch_name")
    if not branch_name:
        thread_id = config.get("configurable", {}).get("thread_id", "unknown")
        branch_name = f"open-swe/{thread_id}"
    return {"branch_name": branch_name}
