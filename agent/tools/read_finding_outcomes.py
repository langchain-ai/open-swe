"""Tool: read this reviewer's past finding outcomes for the repo under analysis.

Surfaces findings that were later confirmed (resolved by a commit / 👍) vs
dismissed (false positive / 👎), so the analyzer can promote the bug patterns
this team actually fixes and add the noisy ones to a skip-list.
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_config

from ..utils.reviewer_outcomes import read_outcomes_for_repo


def read_finding_outcomes(limit: int = 60) -> dict[str, Any]:
    """Return confirmed and dismissed past findings for the repo being analyzed.

    Call this before synthesizing the style prompt. Use the ``confirmed``
    findings to reinforce what to hunt for and the ``dismissed`` findings to
    build the "do not flag" list.
    """
    config = get_config()
    configurable = config.get("configurable") or {}
    full_name = configurable.get("review_style_full_name")
    if not isinstance(full_name, str) or "/" not in full_name:
        return {"ok": False, "error": "no repo under analysis", "confirmed": [], "dismissed": []}

    outcomes = read_outcomes_for_repo(full_name, limit=limit)
    confirmed = outcomes["confirmed"]
    dismissed = outcomes["dismissed"]
    return {
        "ok": True,
        "repo": full_name,
        "counts": {"confirmed": len(confirmed), "dismissed": len(dismissed)},
        "confirmed": confirmed,
        "dismissed": dismissed,
    }
