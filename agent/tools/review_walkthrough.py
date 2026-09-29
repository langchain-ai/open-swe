"""Tools that walk the review guide's chunks; the server renders, checks and tracks every one."""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal, Self

from agent.review_guide.buttons import APPROVE, LOOKS_GOOD, MARK_READY
from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.diff import ChangedLine, FileChange
from agent.review_guide.github import fetch_head
from agent.review_guide.messages import Stage, refresh_progress, retire
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
from agent.run_config import RunConfig
from agent.slack.code_channels import archive_code_channel, set_session_status_result
from agent.slack.tools.reply import slack_reply

MAX_CHANGE_ROWS = 2_000
_ONE_PER_TURN = (
    "you already put something on screen this turn; end your turn and wait for the reader"
)


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

    @property
    def channel_id(self) -> str:
        return self.ctx.session.slack_channel_id

    def status(self) -> str:
        return left_summary(self.left) if self.left else "nothing left but Other"

    def shown_this_turn(self) -> bool:
        run_id = RunConfig.from_runtime().run_id or ""
        return bool(run_id) and self.walk.shown_by_run == run_id

    def mark_shown_this_turn(self) -> None:
        self.walk.shown_by_run = RunConfig.from_runtime().run_id or ""

    async def save(self, stage: Stage | None = None) -> None:
        await self.ctx.session.save_walk(self.walk)
        if stage is not None:
            await refresh_progress(self.ctx.session, self.walk, self.unseen, stage=stage)

    async def retire(self, group: Group | None, note: str) -> None:
        if group is not None:
            await retire(self.channel_id, group.message_ts, group.message_text, note)

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
        "on_screen": (current.title if (current := state.walk.on_screen()) else None),
        "queue": [
            {"position": i, "title": g.title, "lines": _ranges(g)}
            for i, g in enumerate(state.walk.queue, start=1)
        ],
        "changes": "\n".join(rows[:MAX_CHANGE_ROWS]) or "(nothing left to show)",
    }
    if len(rows) > MAX_CHANGE_ROWS:
        result["truncated"] = "pass `paths` to read the rest a few files at a time"
    return result


def _ranges(group: Group) -> str:
    """A chunk's lines as compact per-file ranges, such as ``a.py +3-9 -4``."""
    parts: list[str] = []
    for path in dict.fromkeys(ref.path for ref in group.lines):
        spans: list[str] = []
        for sign in ("+", "-"):
            numbers = sorted(r.lineno for r in group.lines if r.path == path and r.sign == sign)
            runs: list[list[int]] = []
            for number in numbers:
                if runs and number == runs[-1][-1] + 1:
                    runs[-1].append(number)
                else:
                    runs.append([number])
            spans += [f"{sign}{r[0]}" + (f"-{r[-1]}" if len(r) > 1 else "") for r in runs]
        parts.append(f"{path} {' '.join(spans)}")
    return "; ".join(parts)


async def _prepare(
    state: _State,
    title: str,
    show: list[FileRanges],
    explanation: str,
    other: list[FileRanges] | None,
    status: Literal["queued", "shown"],
) -> Group:
    """Claim, render and add a chunk; lines named in ``other`` go to Other first."""
    state.walk.add_other(claim(other, state.left, state.changes) if other else [])
    lines = claim(show, state.left, state.changes)
    prose = await state.ctx.renderer().render(explanation)
    code = await state.ctx.render(lines)
    chunk = Group(title=" ".join(title.split())[:120], lines=lines, status=status)
    chunk.message_text = f"*{chunk.title}*\n{prose}\n\n{code}"
    state.walk.groups.append(chunk)
    return chunk


async def _post(state: _State, chunk: Group, replaced: Group | None) -> dict[str, Any]:
    posted = await slack_reply(chunk.message_text, "progress", options=[LOOKS_GOOD])
    if posted["success"] is not True:
        return posted
    chunk.status = "shown"
    chunk.message_ts = str(posted["message_ts"] or "")
    chunk.run_id = RunConfig.from_runtime().run_id or ""
    state.mark_shown_this_turn()
    await state.retire(replaced, "Replaced by another chunk")
    await state.save()
    return {"success": True, "shown": chunk.title, "queued": len(state.walk.queue)}


async def show_chunk(
    title: str, show: list[FileRanges], explanation: str, other: list[FileRanges] | None = None
) -> dict[str, Any]:
    """Implement the `show_chunk` tool."""
    try:
        state = await _State.load()
        if state.shown_this_turn():
            return {"success": False, "error": _ONE_PER_TURN}
        replaced = state.walk.withdraw()
        chunk = await _prepare(state, title, show, explanation, other, "shown")
    except (GuideUnavailableError, RangeError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    return await _post(state, chunk, replaced)


async def show_queued() -> dict[str, Any]:
    """Implement the `show_queued` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    if state.shown_this_turn():
        return {"success": False, "error": _ONE_PER_TURN}
    if not state.walk.queue:
        return {"success": False, "error": "nothing is queued; use `show_chunk`"}
    replaced = state.walk.withdraw()
    return await _post(state, state.walk.queue[0], replaced)


async def queue_chunk(
    title: str, show: list[FileRanges], explanation: str, other: list[FileRanges] | None = None
) -> dict[str, Any]:
    """Implement the `queue_chunk` tool."""
    try:
        state = await _State.load()
        chunk = await _prepare(state, title, show, explanation, other, "queued")
    except (GuideUnavailableError, RangeError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    await state.save()
    return {
        "success": True,
        "queued": chunk.title,
        "position": len(state.walk.queue),
        "left": state.status(),
    }


async def edit_queue(keep: list[int]) -> dict[str, Any]:
    """Implement the `edit_queue` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    size = len(state.walk.queue)
    bad = [p for p in keep if not 1 <= p <= size]
    if bad or len(set(keep)) != len(keep):
        return {"success": False, "error": f"positions must be distinct and within 1-{size}"}
    state.walk.keep_queue(keep)
    await state.save()
    return {"success": True, "queue": [g.title for g in state.walk.queue], "left": state.status()}


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
            state.walk.add_other(claim(files, state.left, state.changes))
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
        await state.retire(chunk, "✓ Looks good")
    elif state.walk.other_status == "shown":
        await state.ctx.session.mark_seen(state.keys(state.walk.other))
        state.walk.other_status = "approved"
        approved = "Other"
        await retire(
            state.channel_id,
            state.walk.other_message_ts,
            state.walk.other_message_text,
            "✓ Looks good",
        )
    else:
        return {"success": False, "error": "nothing is on screen to approve"}
    await state.save("walking")
    if state.walk.queue:
        following = "show_queued"
    elif state.left:
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
        replaced = state.walk.withdraw()
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
    await state.retire(replaced, "Skipped")
    await state.save("walking")
    return {"success": True, "skipped_lines": len(lines), "left": state.status()}


async def show_other(description: str) -> dict[str, Any]:
    """Implement the `show_other` tool."""
    try:
        state = await _State.load()
        prose = await state.ctx.renderer().render(description)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    if state.shown_this_turn():
        return {"success": False, "error": _ONE_PER_TURN}
    if state.left or state.walk.queue or state.walk.on_screen() is not None:
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
        state.walk.other_message_ts = str(posted["message_ts"] or "")
        state.walk.other_message_text = text
        state.mark_shown_this_turn()
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
    posted = await slack_reply(
        f"{prose}\n\n_{state.walk.coverage()}_", "progress", options=options or None
    )
    await state.save("finished")
    return posted


async def end_walkthrough(message: str) -> dict[str, Any]:
    """Implement the `end_walkthrough` tool."""
    try:
        state = await _State.load()
        prose = await state.ctx.renderer().render(message)
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    posted = await slack_reply(prose, "progress")
    await state.retire(state.walk.withdraw(), "The walkthrough ended")
    await state.save("ended")
    await state.ctx.session.set_closed(True)
    _, status_error = await set_session_status_result(state.channel_id, "closed")
    archived, archive_error = await archive_code_channel(state.channel_id)
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
