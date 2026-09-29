"""Tool: ``approve_review_chunk``. Records the chunk on screen as reviewed and commits it."""

from typing import Any

from agent.review_guide import git
from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.hunks import changed_line_keys

MAX_TITLE_CHARS = 120


async def approve_review_chunk(title: str) -> dict[str, Any]:
    """Implement the `approve_review_chunk` tool."""
    try:
        ctx = await GuideContext.current()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    try:
        staged = await git.staged_diff(ctx.backend, ctx.repo_dir, zero=True)
        if not staged.strip():
            return {"success": False, "error": "nothing is staged"}
        if not await git.shown_matches_index(ctx.backend, ctx.repo_dir):
            return {
                "success": False,
                "error": "the staged changes are not the chunk the reader saw; "
                "show them with `review_reply(chunk=true)` first",
            }
        await ctx.session.mark_seen(changed_line_keys(staged))
        await git.commit_approved(
            ctx.backend, ctx.repo_dir, " ".join(title.split())[:MAX_TITLE_CHARS] or "Reviewed"
        )
        remaining = await git.unstaged_stat(ctx.backend, ctx.repo_dir)
    except git.GuideGitError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "remaining": remaining.strip() or "nothing; every change is reviewed"}
