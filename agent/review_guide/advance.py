"""Keeping the review guide ahead of the reader.

A background prepare run keeps a few chunks queued, fully rendered. When the
reader clicks "Looks good" and nothing else is going on, the server approves
what is on screen and posts the next queued chunk itself, with no model turn in
between, then starts another prepare run to top the queue back up. Anything
the reader says instead goes to the guide as a normal turn, which cancels a
prepare run still working so feedback never waits behind it.
"""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, ValidationError

from agent.prompts import prompt
from agent.review_guide.buttons import LOOKS_GOOD
from agent.review_guide.diff import line_key
from agent.review_guide.github import fetch_head
from agent.review_guide.launch import PREFETCH_KIND, dispatch_guide_run
from agent.review_guide.messages import post_with_buttons, refresh_progress, retire
from agent.review_guide.sessions import ReviewGuideSession
from agent.review_guide.walk import Group
from agent.slack.code_channels import CODE_CHANNEL_SESSION_TS
from agent.source_context import SlackThreadRef
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

QUEUE_DEPTH = 3


class _RunMetadata(BaseModel):
    kind: str = ""


class _Run(BaseModel):
    run_id: str
    metadata: _RunMetadata = _RunMetadata()


async def _active_runs(client: LangGraphClient, thread_id: str) -> list[_Run]:
    runs: list[_Run] = []
    for status in ("running", "pending"):
        for raw in await client.runs.list(thread_id, status=status, limit=10):
            try:
                runs.append(_Run.model_validate(raw))
            except ValidationError:
                logger.warning(
                    "Unreadable run on a review guide thread",
                    extra={"agent_thread_id": thread_id},
                    exc_info=True,
                )
    return runs


async def cancel_prefetch(client: LangGraphClient, thread_id: str) -> None:
    """Stop any prepare run, so what the reader just did is handled first."""
    for run in await _active_runs(client, thread_id):
        if run.metadata.kind == PREFETCH_KIND:
            await client.runs.cancel(thread_id, run.run_id, wait=True, action="interrupt")


def _keys(group: Group) -> Counter[str]:
    return Counter(line_key(ref.path, ref.sign, ref.text) for ref in group.lines)


async def advance(channel_id: str, fallback: Callable[[], Awaitable[object]]) -> None:
    """Handle "Looks good" without the model when a prepared chunk can follow; else ``fallback``."""
    session = await ReviewGuideSession.for_channel(channel_id)
    if session is None or session.closed or session.paused_message_ts:
        await fallback()
        return
    client = langgraph_client()
    runs = await _active_runs(client, session.thread_id)
    if any(run.metadata.kind != PREFETCH_KIND for run in runs):
        await fallback()
        return
    await cancel_prefetch(client, session.thread_id)
    session = await ReviewGuideSession.get(session.thread_id)
    walk = session.walk if session is not None else None
    if session is None or walk is None or not walk.queue:
        await fallback()
        return
    pr = session.pull_request
    head = await fetch_head(pr.owner, pr.repo, pr.number)
    current = walk.on_screen()
    if head is None or head.head.sha != walk.head_sha or current is None:
        await fallback()
        return
    upcoming = walk.queue[0]
    message_ts = await post_with_buttons(session, upcoming.message_text, [LOOKS_GOOD])
    if not message_ts:
        await fallback()
        return
    await session.mark_seen(_keys(current))
    current.status = "approved"
    upcoming.status = "shown"
    upcoming.message_ts = message_ts
    await session.save_walk(walk)
    await retire(channel_id, current.message_ts, current.message_text, "✓ Looks good")
    await refresh_progress(session, walk)
    logger.info(
        "Review guide showed a prepared chunk",
        extra={"agent_thread_id": session.thread_id, "guide_queued": len(walk.queue)},
    )
    await start_prefetch(session)


async def start_prefetch(session: ReviewGuideSession) -> None:
    """Start a prepare run when the queue has room and nothing else is running."""
    walk = session.walk
    if session.closed or session.paused_message_ts or walk is None:
        return
    if len(walk.queue) >= QUEUE_DEPTH:
        return
    client = langgraph_client()
    if await _active_runs(client, session.thread_id):
        return
    await dispatch_guide_run(
        session.thread_id,
        SlackThreadRef(channel_id=session.slack_channel_id, thread_ts=CODE_CHANNEL_SESSION_TS),
        prompt("review-guide/prefetch", depth=QUEUE_DEPTH),
        workspace_slug=session.workspace_slug,
        prefetch=True,
    )


async def top_up_after_run(thread_id: str, kind: str) -> None:
    """After a normal guide turn, get the next chunks ready in the background."""
    if kind == PREFETCH_KIND:
        return
    session = await ReviewGuideSession.get(thread_id)
    if session is not None:
        await start_prefetch(session)
