"""Tools that walk the review guide's chunks; the server renders, checks and tracks every one."""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Self

from agent.review_guide.buttons import APPROVE, LOOKS_GOOD, MARK_READY
from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.diff import ChangedLine, FileChange
from agent.review_guide.github import fetch_head
from agent.review_guide.render import RenderError, fenced
from agent.review_guide.walk import (
    FileRanges,
    Group,
    LineRef,
    RangeError,
    Walk,
    claim,
    other_stat,
)
from agent.review_guide.walk import summary as left_summary
from agent.slack.code_channels import archive_code_channel, set_session_status_result
from agent.slack.tools.reply import slack_reply

MAX_CHANGE_ROWS = 2_000


@dataclass
class _State:
    ctx: GuideContext
    changes: list[FileChange]
    unseen: list[ChangedLine]
    walk: Walk

    @classmethod
    async def load(cls) -> Self:
        ctx = await GuideContext.current()
        changes = await ctx.changes()
        return cls(
            ctx=ctx, changes=changes, unseen=await ctx.unseen(changes), walk=ctx.walk(changes)
        )

    @property
    def left(self) -> list[ChangedLine]:
        return self.walk.left(self.unseen)

    def status(self) -> str:
        return left_summary(self.left) if self.left else "nothing left but Other"

    async def save(self) -> None:
        await self.ctx.session.save_walk(self.walk)

    def keys(self, refs: list[LineRef]) -> Counter[str]:
        wanted = set(refs)
        return Counter(
            line.key
            for change in self.changes
            for line in change.lines
            if LineRef.of(line) in wanted
        )


async def read_changes(paths: list[str] | None = None) -> dict[str, Any]:
    """Implement the `read_changes` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    wanted = set(paths or [])
    left = state.left
    rows: list[str] = []
    for change in state.changes:
        if wanted and change.path not in wanted:
            continue
        lines = [line for line in left if line.path == change.path]
        if lines:
            rows.append(f"=== {change.stat(lines)}")
            rows += [f"{line.sign}{line.lineno}\t{line.text}" for line in lines]
    result: dict[str, Any] = {
        "success": True,
        "left": state.status(),
        "other_lines": len(state.walk.other),
        "changes": "\n".join(rows[:MAX_CHANGE_ROWS]) or "(nothing left to show)",
    }
    if len(rows) > MAX_CHANGE_ROWS:
        result["truncated"] = "pass `paths` to read the rest a few files at a time"
    return result


async def show_chunk(
    title: str, show: list[FileRanges], explanation: str, other: list[FileRanges] | None = None
) -> dict[str, Any]:
    """Implement the `show_chunk` tool."""
    try:
        state = await _State.load()
        state.walk.withdraw()
        moved = claim(other, state.left, state.changes) if other else []
        state.walk.other += moved
        lines = claim(show, state.left, state.changes)
        prose = await state.ctx.renderer().render(explanation)
        code = await state.ctx.render(lines)
    except (GuideUnavailableError, RangeError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    chunk = Group(title=" ".join(title.split())[:120], lines=lines, status="shown")
    state.walk.groups.append(chunk)
    footer = (
        f"_{left_summary(state.left)} after this_"
        if state.left
        else "_Only Other is left after this_"
    )
    posted = await slack_reply(
        f"*{chunk.title}*\n{prose}\n\n{code}\n{footer}", "progress", options=[LOOKS_GOOD]
    )
    if posted["success"] is not True:
        return posted
    await state.save()
    return {"success": True, "shown_lines": len(lines), "moved_to_other": len(moved)}


async def move_to_other(files: list[FileRanges], back: bool = False) -> dict[str, Any]:
    """Implement the `move_to_other` tool."""
    try:
        state = await _State.load()
        if back:
            other = set(state.walk.other)
            pool = [line for line in state.unseen if LineRef.of(line) in other]
            refs = set(claim(files, pool, state.changes))
            state.walk.other = [ref for ref in state.walk.other if ref not in refs]
        else:
            state.walk.other += claim(files, state.left, state.changes)
    except (GuideUnavailableError, RangeError) as exc:
        return {"success": False, "error": str(exc)}
    await state.save()
    return {"success": True, "other_lines": len(state.walk.other), "left": state.status()}


async def approve_review_chunk() -> dict[str, Any]:
    """Implement the `approve_review_chunk` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    chunk = state.walk.on_screen()
    if chunk is not None:
        await state.ctx.session.mark_seen(state.keys(chunk.lines))
        chunk.status = "approved"
        approved = chunk.title
    elif state.walk.other_status == "shown":
        await state.ctx.session.mark_seen(state.keys(state.walk.other))
        state.walk.other_status = "approved"
        approved = "Other"
    else:
        return {"success": False, "error": "nothing is on screen to approve"}
    await state.save()
    if state.left:
        following = "show_chunk"
    elif state.walk.has_other and state.walk.other_status == "open":
        following = "show_other"
    else:
        following = "finish_walkthrough"
    return {"success": True, "approved": approved, "left": state.status(), "next": following}


async def skip_changes(
    reason: str, files: list[FileRanges] | None = None, include_other: bool = False
) -> dict[str, Any]:
    """Implement the `skip_changes` tool."""
    try:
        state = await _State.load()
        state.walk.withdraw()
        lines = (
            claim(files, state.left, state.changes)
            if files
            else [LineRef.of(line) for line in state.left]
        )
    except (GuideUnavailableError, RangeError) as exc:
        return {"success": False, "error": str(exc)}
    if lines:
        state.walk.groups.append(
            Group(title="Skipped", lines=lines, status="skipped", reason=reason.strip())
        )
    if include_other and state.walk.other_status in ("open", "shown"):
        state.walk.other_status = "skipped"
    await state.save()
    return {"success": True, "skipped_lines": len(lines), "left": state.status()}


async def show_other(description: str) -> dict[str, Any]:
    """Implement the `show_other` tool."""
    try:
        state = await _State.load()
        prose = await state.ctx.renderer().render(description)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    if state.left or state.walk.on_screen() is not None:
        return {
            "success": False,
            "error": f"show, move to Other or skip everything first: {state.status()}",
        }
    if not state.walk.has_other:
        state.walk.other_status = "approved"
        await state.save()
        return {"success": True, "note": "Other is empty; call `finish_walkthrough`"}
    text = f"*Other · {len(state.walk.other)} lines*\n{prose}\n\n{fenced(other_stat(state.walk))}"
    posted = await slack_reply(text, "progress", options=[LOOKS_GOOD])
    if posted["success"] is True:
        state.walk.other_status = "shown"
        await state.save()
    return posted


async def finish_walkthrough(summary: str) -> dict[str, Any]:
    """Implement the `finish_walkthrough` tool."""
    try:
        state = await _State.load()
        prose = await state.ctx.renderer().render(summary)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    left = state.walk.unfinished(state.unseen)
    if left:
        return {
            "success": False,
            "error": "the walkthrough is not finished: "
            + "; ".join(left)
            + ". Show it, move it to Other, or skip it if the reader asked to",
        }
    options: list[str] = []
    if state.ctx.session.mode == "reviewer":
        options.append(APPROVE)
    else:
        pr = state.ctx.session.pull_request
        head = await fetch_head(pr.owner, pr.repo, pr.number)
        if head is not None and head.draft:
            options.append(MARK_READY)
    return await slack_reply(
        f"{prose}\n\n_{state.walk.coverage()}_", "progress", options=options or None
    )


async def end_walkthrough(message: str) -> dict[str, Any]:
    """Implement the `end_walkthrough` tool."""
    try:
        ctx = await GuideContext.current()
        prose = await ctx.renderer().render(message)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    posted = await slack_reply(prose, "progress")
    await ctx.session.set_closed(True)
    channel_id = ctx.session.slack_channel_id
    _, status_error = await set_session_status_result(channel_id, "closed")
    archived, archive_error = await archive_code_channel(channel_id)
    warnings = [
        f"could not {what}: {error}"
        for what, error in (
            ("post the message", None if posted["success"] is True else str(posted["error"])),
            ("close the session", status_error),
            ("archive the channel", None if archived else archive_error),
        )
        if error
    ]
    return {"success": True, "ended": True, **({"warnings": warnings} if warnings else {})}
