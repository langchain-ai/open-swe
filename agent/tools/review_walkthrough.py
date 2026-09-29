"""Tools that plan and walk the review guide's chunks; the server renders and tracks every one."""

from collections import Counter
from typing import Any

from agent.review_guide.buttons import APPROVE, LOOKS_GOOD, MARK_READY
from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.github import fetch_head
from agent.review_guide.plan import ChunkSpec, LineRef, Plan, PlanError, build_plan, other_stat
from agent.review_guide.render import RenderError, fenced
from agent.slack.tools.reply import slack_reply

MAX_CHANGE_ROWS = 2_000


async def _context_and_plan() -> tuple[GuideContext, Plan]:
    ctx = await GuideContext.current()
    plan = ctx.session.plan
    if plan is None or plan.head_sha != ctx.head_sha:
        raise GuideUnavailableError(
            "there is no plan for the pull request's current head; call `plan_walkthrough`"
        )
    return ctx, plan


async def _keys(ctx: GuideContext, refs: list[LineRef]) -> Counter[str]:
    wanted = set(refs)
    return Counter(
        line.key
        for change in await ctx.changes()
        for line in change.lines
        if LineRef.of(line) in wanted
    )


async def read_changes(paths: list[str] | None = None) -> dict[str, Any]:
    """Implement the `read_changes` tool."""
    try:
        ctx = await GuideContext.current()
        changes = await ctx.changes()
        left = await ctx.unseen(changes)
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    wanted = set(paths or [])
    rows: list[str] = []
    for change in changes:
        if wanted and change.path not in wanted:
            continue
        lines = [line for line in left if line.path == change.path]
        if not lines and not change.textless:
            continue
        rows.append(f"=== {change.stat(lines)}")
        rows += [f"{line.sign}{line.lineno}\t{line.text}" for line in lines]
    if len(rows) > MAX_CHANGE_ROWS:
        return {
            "success": True,
            "truncated": True,
            "changes": "\n".join(rows[:MAX_CHANGE_ROWS]),
            "hint": "pass `paths` to read the rest a few files at a time",
        }
    return {"success": True, "changes": "\n".join(rows) or "(nothing left to review)"}


async def plan_walkthrough(chunks: list[ChunkSpec]) -> dict[str, Any]:
    """Implement the `plan_walkthrough` tool."""
    try:
        ctx = await GuideContext.current()
        changes = await ctx.changes()
        plan = build_plan(ctx.head_sha, changes, await ctx.unseen(changes), chunks)
    except (GuideUnavailableError, PlanError) as exc:
        return {"success": False, "error": str(exc)}
    await ctx.session.save_plan(plan)
    return {
        "success": True,
        "chunks": [
            {"number": i + 1, "title": chunk.title, "lines": len(chunk.lines)}
            for i, chunk in enumerate(plan.chunks)
        ],
        "other_lines": len(plan.other),
        "other": other_stat(plan) or "(empty)",
    }


async def show_next_chunk(explanation: str) -> dict[str, Any]:
    """Implement the `show_next_chunk` tool."""
    try:
        ctx, plan = await _context_and_plan()
        index = plan.next_chunk()
        if index is None:
            return {"success": False, "error": "every chunk is done; call `show_other` next"}
        chunk = plan.chunks[index]
        prose = await ctx.renderer().render(explanation)
        code = await ctx.render(chunk.lines)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    text = f"*{index + 1}/{len(plan.chunks)} · {chunk.title}*\n{prose}\n\n{code}"
    posted = await slack_reply(text, "progress", options=[LOOKS_GOOD])
    if posted["success"] is True:
        chunk.status = "shown"
        await ctx.session.save_plan(plan)
    return posted


async def show_other(description: str) -> dict[str, Any]:
    """Implement the `show_other` tool."""
    try:
        ctx, plan = await _context_and_plan()
        prose = await ctx.renderer().render(description)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    if any(chunk.status in ("planned", "shown") for chunk in plan.chunks):
        return {"success": False, "error": "show or skip every chunk before Other"}
    if not plan.has_other:
        plan.other_status = "approved"
        await ctx.session.save_plan(plan)
        return {"success": True, "note": "Other is empty; call `finish_walkthrough`"}
    text = f"*Other · {len(plan.other)} lines*\n{prose}\n\n{fenced(other_stat(plan))}"
    posted = await slack_reply(text, "progress", options=[LOOKS_GOOD])
    if posted["success"] is True:
        plan.other_status = "shown"
        await ctx.session.save_plan(plan)
    return posted


async def approve_review_chunk() -> dict[str, Any]:
    """Implement the `approve_review_chunk` tool."""
    try:
        ctx, plan = await _context_and_plan()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    index = plan.on_screen()
    if index is not None:
        chunk = plan.chunks[index]
        await ctx.session.mark_seen(await _keys(ctx, chunk.lines))
        chunk.status = "approved"
        approved = f"chunk {index + 1}"
    elif plan.other_status == "shown":
        await ctx.session.mark_seen(await _keys(ctx, plan.other))
        plan.other_status = "approved"
        approved = "Other"
    else:
        return {"success": False, "error": "nothing is on screen to approve"}
    await ctx.session.save_plan(plan)
    upcoming = plan.next_chunk()
    if upcoming is not None:
        following = "show_next_chunk"
    elif plan.has_other and plan.other_status == "planned":
        following = "show_other"
    else:
        following = "finish_walkthrough"
    return {"success": True, "approved": approved, "next": following}


async def skip_review_chunks(
    chunks: list[int], reason: str, skip_other: bool = False
) -> dict[str, Any]:
    """Implement the `skip_review_chunks` tool."""
    try:
        ctx, plan = await _context_and_plan()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    bad = [n for n in chunks if not 1 <= n <= len(plan.chunks)]
    if bad:
        return {"success": False, "error": f"no such chunks: {bad}"}
    for number in chunks:
        chunk = plan.chunks[number - 1]
        if chunk.status in ("planned", "shown"):
            chunk.status = "skipped"
            chunk.skip_reason = reason.strip()
    if skip_other and plan.other_status in ("planned", "shown"):
        plan.other_status = "skipped"
    await ctx.session.save_plan(plan)
    return {"success": True, "left": plan.unfinished()}


async def finish_walkthrough(summary: str) -> dict[str, Any]:
    """Implement the `finish_walkthrough` tool."""
    try:
        ctx, plan = await _context_and_plan()
        prose = await ctx.renderer().render(summary)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    left = plan.unfinished()
    if left:
        return {
            "success": False,
            "error": "the walkthrough is not finished: "
            + "; ".join(left)
            + ". Show them, or skip them if the reader asked to",
        }
    options: list[str] = []
    if ctx.session.mode == "reviewer":
        options.append(APPROVE)
    else:
        pr = ctx.session.pull_request
        head = await fetch_head(pr.owner, pr.repo, pr.number)
        if head is not None and head.draft:
            options.append(MARK_READY)
    return await slack_reply(f"{prose}\n\n_{plan.coverage()}_", "progress", options=options or None)
