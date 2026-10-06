"""Tools that walk the review guide's chunks; the server renders, checks and tracks every one."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Literal, Self

from agent.review_guide.buttons import APPROVE, LOOKS_GOOD, MARK_READY
from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.diff import ChangedLine, FileChange
from agent.review_guide.github import fetch_head
from agent.review_guide.messages import Stage, refresh_progress, retire
from agent.review_guide.render import RenderError, fenced
from agent.review_guide.sessions import GuideMode
from agent.review_guide.walk import (
    FileRanges,
    Group,
    LineRef,
    RangeError,
    Walk,
    claim,
    other_stat,
)
from agent.run_config import RunConfig
from agent.slack.code_channels import archive_code_channel, set_session_status_result
from agent.slack.http import SlackRequestError
from agent.slack.tools.reply import slack_reply
from agent.tools.approve_pull_request import approve_pull_request
from agent.tools.mark_pull_request_ready import mark_pull_request_ready
from agent.tools.record_author_feedback import record_author_feedback

_ONE_PER_TURN = (
    "you already put something on screen this turn; end your turn and wait for the reader"
)
# Each tool reads the whole walk and writes it back, and one model message's tool calls run
# concurrently, so they take turns per thread or the last write drops the others.
_WALK_LOCKS: dict[str, asyncio.Lock] = {}


def _one_at_a_time[**P](
    tool: Callable[P, Awaitable[dict[str, Any]]],
) -> Callable[P, Awaitable[dict[str, Any]]]:
    @wraps(tool)
    async def serialized(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        thread_id = RunConfig.from_runtime().thread_id or ""
        async with _WALK_LOCKS.setdefault(thread_id, asyncio.Lock()):
            return await tool(*args, **kwargs)

    return serialized


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
        walk = ctx.walk(changes)
        return cls(ctx=ctx, changes=changes, unseen=await ctx.unseen(changes, walk), walk=walk)

    @property
    def left(self) -> list[ChangedLine]:
        return self.walk.left(self.unseen)

    @property
    def channel_id(self) -> str:
        return self.ctx.session.slack_channel_id

    def report(self, **result: object) -> dict[str, Any]:
        """A successful result, with where the walkthrough now stands."""
        return {
            "success": True,
            **result,
            "walkthrough": self.walk.status(self.unseen).model_dump(),
        }

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
    return state.report(shown=chunk.title)


@_one_at_a_time
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


@_one_at_a_time
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


@_one_at_a_time
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
    return state.report(queued=chunk.title)


@_one_at_a_time
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
    return state.report()


@_one_at_a_time
async def move_to_other(files: list[FileRanges], restore: bool = False) -> dict[str, Any]:
    """Implement the `move_to_other` tool."""
    try:
        state = await _State.load()
        if restore:
            other = set(state.walk.other)
            pool = [line for line in state.unseen if LineRef.of(line) in other]
            refs = set(claim(files, pool, state.changes))
            state.walk.other = [ref for ref in state.walk.other if ref not in refs]
        else:
            state.walk.add_other(claim(files, state.left, state.changes))
    except (GuideUnavailableError, RangeError) as exc:
        return {"success": False, "error": str(exc)}
    await state.save()
    return state.report()


@_one_at_a_time
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
    return state.report(skipped_lines=len(lines))


@_one_at_a_time
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
            "error": "show, move to Other or skip everything else first",
            "walkthrough": state.walk.status(state.unseen).model_dump(),
        }
    if not state.walk.has_other:
        state.walk.other_status = "approved"
        await state.save()
        return state.report(note="Other is empty; call `end_walkthrough`")
    text = f"*Other · {len(state.walk.other)} lines*\n{prose}\n\n{fenced(other_stat(state.walk))}"
    posted = await slack_reply(text, "progress", options=[LOOKS_GOOD])
    if posted["success"] is not True:
        return posted
    state.walk.other_status = "shown"
    state.walk.other_message_ts = str(posted["message_ts"] or "")
    state.walk.other_message_text = text
    state.mark_shown_this_turn()
    await state.save()
    return state.report(shown="Other")


@_one_at_a_time
async def end_walkthrough(message: str = "", archive: bool = False) -> dict[str, Any]:
    """Implement the `end_walkthrough` tool."""
    try:
        state = await _State.load()
        prose = await state.ctx.renderer().render(message) if message.strip() else ""
    except (GuideUnavailableError, RenderError) as exc:
        return {"success": False, "error": str(exc)}
    finished = not state.walk.unfinished(state.unseen)
    options: list[str] = []
    if finished and not archive:
        if state.ctx.session.mode == "reviewer":
            options.append(APPROVE)
        else:
            pr = state.ctx.session.pull_request
            head = await fetch_head(pr.owner, pr.repo, pr.number)
            if head is not None and head.draft:
                options.append(MARK_READY)
    warnings: list[str] = []
    body = "\n\n".join(
        part for part in (prose, f"_{state.walk.coverage()}_" if finished else "") if part
    )
    if prose or options:
        posted = await slack_reply(body, "progress", options=options or None)
        if posted["success"] is not True:
            warnings.append(f"could not post the message: {posted['error']}")
    await state.retire(state.walk.withdraw(), "The walkthrough ended")
    await state.save("finished" if finished else "ended")
    if archive:
        await state.ctx.session.set_closed(True)
        for what, close in (
            ("close the session", set_session_status_result(state.channel_id, "closed")),
            ("archive the channel", archive_code_channel(state.channel_id)),
        ):
            try:
                await close
            except SlackRequestError as exc:
                warnings.append(f"could not {what}: {exc}")
    return {
        "success": True,
        "finished": finished,
        "archived": archive,
        **({"warnings": warnings} if warnings else {}),
    }


def walkthrough_tools(mode: GuideMode, *, prefetch: bool) -> list[Callable[..., Awaitable[Any]]]:
    """The tools a review walkthrough adds; a prepare run gets only those that post nothing."""
    if prefetch:
        return [queue_chunk, edit_queue, move_to_other]
    closing: list[Callable[..., Awaitable[Any]]] = (
        [mark_pull_request_ready, record_author_feedback]
        if mode == "author"
        else [approve_pull_request]
    )
    return [
        show_chunk,
        show_queued,
        queue_chunk,
        edit_queue,
        move_to_other,
        skip_changes,
        show_other,
        end_walkthrough,
        *closing,
    ]
