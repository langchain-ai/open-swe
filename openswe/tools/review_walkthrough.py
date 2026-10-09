"""Tools that walk a reader through the shared plan in a review guide channel.

The server renders, checks and tracks everything: chunks come from the plan,
and the reader's walk records what is on screen, approved and skipped. A guide
that gets ahead of the review scout places the next chunk itself with the same
planning tools the scout uses.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Self

from openswe.review_guide.buttons import APPROVE, LOOKS_GOOD, MARK_READY
from openswe.review_guide.context import GuideContext, GuideUnavailableError
from openswe.review_guide.github import fetch_head
from openswe.review_guide.messages import Stage, refresh_progress, retire
from openswe.review_guide.sessions import GuideMode
from openswe.review_guide.walk import OnScreen, Reader, Walk
from openswe.run_config import RunConfig
from openswe.slack.code_channels import archive_code_channel, set_session_status_result
from openswe.slack.http import SlackRequestError
from openswe.slack.tools.reply import slack_reply
from openswe.tools.approve_pull_request import approve_pull_request
from openswe.tools.mark_pull_request_ready import mark_pull_request_ready
from openswe.tools.plan_walkthrough import PLANNING_ERRORS
from openswe.tools.record_author_feedback import record_author_feedback
from openswe.walkthrough.plan import (
    MAX_EXPLANATION_CHARS,
    MAX_TITLE_CHARS,
    FileRanges,
    LineRef,
    claim,
)

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
    walk: Walk
    reader: Reader

    @classmethod
    async def load(cls) -> Self:
        ctx = await GuideContext.current()
        walk = ctx.walk()
        return cls(ctx=ctx, walk=walk, reader=await ctx.reader(walk))

    async def reload(self) -> None:
        """Read the plan again after this turn changed it."""
        self.reader = await self.ctx.reader(self.walk)

    @property
    def channel_id(self) -> str:
        return self.ctx.session.slack_channel_id

    def report(self, **result: object) -> dict[str, Any]:
        """A successful result, with where the reader now stands."""
        return {
            "success": True,
            **result,
            "walkthrough": self.reader.status(self.ctx.unplanned).model_dump(),
        }

    def shown_this_turn(self) -> bool:
        run_id = RunConfig.from_runtime().run_id or ""
        return bool(run_id) and self.walk.shown_by_run == run_id

    async def save(self, stage: Stage | None = None) -> None:
        await self.ctx.session.save_walk(self.walk)
        if stage is not None:
            await refresh_progress(self.ctx.session, self.reader, stage=stage)

    async def retire(self, shown: OnScreen | None, note: str) -> None:
        if shown is not None:
            await retire(self.channel_id, shown.message_ts, shown.message_text, note)

    async def post(self, shown: OnScreen) -> dict[str, Any]:
        """Put ``shown`` on screen with its "Looks good" button, replacing what was there."""
        posted = await slack_reply(shown.message_text, "progress", options=[LOOKS_GOOD])
        if posted["success"] is not True:
            return posted
        replaced = self.walk.withdraw()
        shown.message_ts = str(posted["message_ts"] or "")
        shown.run_id = RunConfig.from_runtime().run_id or ""
        self.walk.on_screen = shown
        self.walk.shown_by_run = shown.run_id
        await self.retire(replaced, "Replaced by another chunk")
        await self.save("walking")
        return self.report(shown=shown.title)


@_one_at_a_time
async def walkthrough_show_chunk(number: int | None = None) -> dict[str, Any]:
    """Implement the `walkthrough_show_chunk` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    if state.shown_this_turn():
        return {"success": False, "error": _ONE_PER_TURN}
    chunk = state.reader.chunk(number) if number is not None else state.reader.next_chunk()
    if chunk is None:
        error = (
            f"there is no chunk {number}"
            if number is not None
            else "no planned chunk is left for the reader; place the next one with `walkthrough_plan_chunk`"
        )
        return {"success": False, "error": error, **state.report()}
    if not state.reader.remaining(chunk.lines):
        return {"success": False, "error": "the reader already approved or skipped that chunk"}
    return await state.post(state.reader.show(chunk))


@_one_at_a_time
async def walkthrough_show_lines(
    title: str, show: list[FileRanges], explanation: str
) -> dict[str, Any]:
    """Implement the `walkthrough_show_lines` tool."""
    try:
        state = await _State.load()
        left = set(state.reader.remaining(state.reader.unseen))
        candidates = [
            line
            for change in state.ctx.workspace.changes
            for line in change.lines
            if LineRef.of(line) in left
        ]
        lines = claim(show, candidates)
        code = await state.ctx.workspace.checkout.render(lines, state.ctx.workspace.changes)
    except (GuideUnavailableError, *PLANNING_ERRORS) as exc:
        return {"success": False, "error": str(exc)}
    if state.shown_this_turn():
        return {"success": False, "error": _ONE_PER_TURN}
    trimmed = " ".join(title.split())[:MAX_TITLE_CHARS] or "Requested lines"
    return await state.post(
        state.reader.custom(trimmed, explanation.strip()[:MAX_EXPLANATION_CHARS], lines, code)
    )


@_one_at_a_time
async def walkthrough_order_chunks(chunks: list[int]) -> dict[str, Any]:
    """Implement the `walkthrough_order_chunks` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    picked = [state.reader.chunk(n) for n in chunks]
    if any(chunk is None for chunk in picked) or len(set(chunks)) != len(chunks):
        return {"success": False, "error": "chunks must be distinct numbers from the status"}
    state.walk.order = [chunk.id for chunk in picked if chunk is not None]
    await state.save()
    return state.report()


@_one_at_a_time
async def walkthrough_show_other() -> dict[str, Any]:
    """Implement the `walkthrough_show_other` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    if state.shown_this_turn():
        return {"success": False, "error": _ONE_PER_TURN}
    if reasons := state.reader.unfinished(state.ctx.unplanned, other=False):
        return {"success": False, "error": "; ".join(reasons), **state.report()}
    if not state.reader.other_pending:
        return state.report(note="Other has nothing left for the reader; call `walkthrough_end`")
    return await state.post(state.reader.show_other())


@_one_at_a_time
async def walkthrough_skip_changes(
    reason: str, chunks: list[int] | None = None, include_other: bool = False
) -> dict[str, Any]:
    """Implement the `walkthrough_skip_changes` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    if chunks is None:
        picked = [*state.reader.plan.chunks]
        extra = state.reader.remaining(state.ctx.unplanned)
    else:
        bad = [n for n in chunks if state.reader.chunk(n) is None]
        if bad:
            return {"success": False, "error": f"no such chunks: {bad}"}
        picked = [chunk for n in chunks if (chunk := state.reader.chunk(n)) is not None]
        extra = []
    lines = [ref for chunk in picked for ref in state.reader.remaining(chunk.lines)] + extra
    current = state.walk.on_screen
    replaced = (
        state.walk.withdraw()
        if current is not None and (chunks is None or current.chunk_id in {c.id for c in picked})
        else None
    )
    state.walk.skip(reason, lines, other=include_other)
    await state.reload()
    await state.retire(replaced, "Skipped")
    await state.save("walking")
    return state.report(skipped_lines=len(lines))


async def _plan(
    change: Callable[[GuideContext], Awaitable[object]],
) -> dict[str, Any]:
    try:
        state = await _State.load()
        result = await change(state.ctx)
    except (GuideUnavailableError, *PLANNING_ERRORS) as exc:
        return {"success": False, "error": str(exc)}
    await state.reload()
    return state.report(
        **({"chunk": result} if isinstance(result, int) else {}),
        plan=state.ctx.workspace.status().model_dump(),
    )


@_one_at_a_time
async def walkthrough_plan_chunk(
    title: str,
    show: list[FileRanges],
    explanation: str,
    other: list[FileRanges] | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    """Implement the `walkthrough_plan_chunk` tool."""
    return await _plan(
        lambda ctx: ctx.workspace.plan_chunk(
            title=title, show=show, explanation=explanation, other=other, after=after
        )
    )


@_one_at_a_time
async def walkthrough_move_to_other(
    files: list[FileRanges], restore: bool = False
) -> dict[str, Any]:
    """Implement the `walkthrough_move_to_other` tool."""
    return await _plan(lambda ctx: ctx.workspace.move_to_other(files, restore=restore))


@_one_at_a_time
async def walkthrough_describe_other(summary: str) -> dict[str, Any]:
    """Implement the `walkthrough_describe_other` tool."""
    return await _plan(lambda ctx: ctx.workspace.describe_other(summary))


@_one_at_a_time
async def walkthrough_end(message: str = "", archive: bool = False) -> dict[str, Any]:
    """Implement the `walkthrough_end` tool."""
    try:
        state = await _State.load()
    except GuideUnavailableError as exc:
        return {"success": False, "error": str(exc)}
    finished = not state.reader.unfinished(state.ctx.unplanned)
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
        part for part in (message.strip(), f"_{state.walk.coverage()}_" if finished else "") if part
    )
    if body or options:
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


def walkthrough_tools(mode: GuideMode) -> list[Callable[..., Awaitable[Any]]]:
    """The tools a review walkthrough adds."""
    closing: list[Callable[..., Awaitable[Any]]] = (
        [mark_pull_request_ready, record_author_feedback]
        if mode == "author"
        else [approve_pull_request]
    )
    return [
        walkthrough_show_chunk,
        walkthrough_show_lines,
        walkthrough_order_chunks,
        walkthrough_show_other,
        walkthrough_skip_changes,
        walkthrough_plan_chunk,
        walkthrough_move_to_other,
        walkthrough_describe_other,
        walkthrough_end,
        *closing,
    ]
