from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..dashboard.review_styles import append_repo_prompt_learning, normalize_repo_full_name


def update_repo_prompt(learning: str, source: str = "") -> dict[str, Any]:
    """Append a durable repository-specific learning to the reviewer prompt.

    Use this after a human replies to an Open SWE PR review comment and teaches a
    repo convention, review preference, or recurring false-positive pattern that
    should influence future reviews. Store a concise synthesized rule, not the raw
    comment text.
    """
    if not learning.strip():
        return {"ok": False, "error": "learning cannot be empty"}

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    repo_config = configurable.get("repo") if isinstance(configurable, dict) else None
    if not isinstance(repo_config, dict):
        return {"ok": False, "error": "Missing repo in run config"}

    owner = repo_config.get("owner")
    repo = repo_config.get("name")
    if not isinstance(owner, str) or not owner or not isinstance(repo, str) or not repo:
        return {"ok": False, "error": "Missing repo owner or name in run config"}

    try:
        full_name = normalize_repo_full_name(f"{owner}/{repo}")
        record = asyncio.run(
            append_repo_prompt_learning(
                full_name,
                learning,
                source=source,
            )
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "full_name": full_name,
        "status": record.get("status"),
        "custom_prompt": record.get("custom_prompt"),
    }
