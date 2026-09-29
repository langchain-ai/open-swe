"""Tool: ``review_reply``. Posts a review guide message, rendered from Jinja against the checkout."""

from typing import Any

from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.render import RenderError
from agent.slack.tools.reply import slack_reply


async def review_reply(message: str, options: list[str] | None = None) -> dict[str, Any]:
    """Implement the `review_reply` tool."""
    try:
        ctx = await GuideContext.current()
        text = await ctx.renderer().render(message)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    return await slack_reply(text, "progress", options=options or None)
