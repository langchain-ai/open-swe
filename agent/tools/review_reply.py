"""Tool: ``review_reply``. Posts a review guide message, rendered from Jinja against the checkout."""

from typing import Any

from agent.review_guide import git
from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.render import MessageRenderer, RenderError
from agent.slack.tools.reply import slack_reply

LOOKS_GOOD = "Looks good"


async def review_reply(
    message: str, chunk: bool = False, options: list[str] | None = None
) -> dict[str, Any]:
    """Implement the `review_reply` tool."""
    try:
        ctx = await GuideContext.current()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    renderer = MessageRenderer(ctx.backend, ctx.repo_dir)
    try:
        text = await renderer.render(message)
    except RenderError as exc:
        return {"success": False, "error": f"message template failed to render: {exc}"}
    buttons = list(options or [])
    if chunk:
        if not (await git.staged_diff(ctx.backend, ctx.repo_dir)).strip():
            return {"success": False, "error": "nothing is staged; stage the chunk first"}
        if not renderer.quoted_chunk:
            return {
                "success": False,
                "error": "a chunk message must quote the chunk with {{ staged() }} or {{ stat() }}",
            }
        await git.mark_shown(ctx.backend, ctx.repo_dir)
        buttons = [LOOKS_GOOD, *(b for b in buttons if b != LOOKS_GOOD)]
    return await slack_reply(text, "progress", options=buttons or None)
