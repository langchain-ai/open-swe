"""Tool: ``record_author_feedback``. Folds the author's walkthrough feedback into the PR's human input."""

from typing import Any

from openswe.review_guide.context import GuideContext, GuideUnavailableError
from openswe.tools.record_human_input import MAX_SUMMARY_CHARS
from openswe.walkthrough.record import Walkthrough


async def record_author_feedback(summary: str) -> dict[str, Any]:
    """Implement the `record_author_feedback` tool."""
    trimmed = summary.strip()[:MAX_SUMMARY_CHARS]
    if not trimmed:
        return {"success": False, "error": "summary is empty; nothing recorded"}
    try:
        ctx = await GuideContext.current()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    if ctx.session.mode != "author":
        return {"success": False, "error": "only the author's walkthrough records human input"}
    if not await Walkthrough.set_human_input(ctx.session.pull_request_id, trimmed):
        # The scout reads this conversation when it builds the walkthrough, so nothing is lost.
        return {"success": True, "recorded": "the review page has no walkthrough yet"}
    return {"success": True, "recorded": "the review page's human input"}
