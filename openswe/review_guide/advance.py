"""The reader's "Looks good", and keeping the review guide ahead of them.

Only the button approves. A click approves what it was clicked on right away,
then shows the next prepared chunk itself, with no model turn in between, and
starts a background prepare run to top the queue back up. When nothing is
prepared, or a turn is already running, the guide gets a turn to pick what
comes next; a click that lands during a turn is recorded when that turn begins.
Anything the reader types goes to the guide as a normal turn, which cancels a
prepare run still working so feedback never waits behind it.
"""

import logging
from collections import Counter

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, ValidationError

from openswe.prompts import prompt
from openswe.review_guide.buttons import LOOKS_GOOD
from openswe.review_guide.diff import line_key
from openswe.review_guide.github import fetch_head
from openswe.review_guide.launch import PREFETCH_KIND, dispatch_guide_run
from openswe.review_guide.messages import post_with_buttons, refresh_progress, retire
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.review_guide.walk import Group, Walk
from openswe.utils.thread_ops import langgraph_client

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


async def approve_click(session: ReviewGuideSession, walk: Walk, message_ts: str) -> Group | None:
    """Record "Looks good" on ``message_ts`` if it is still on screen; returns what it approved."""
    approved = walk.approve(message_ts)
    if approved is None:
        return None
    await session.mark_seen(
        Counter(line_key(ref.path, ref.sign, ref.text) for ref in approved.lines)
    )
    await session.save_walk(walk)
    await retire(
        session.slack_channel_id, approved.message_ts, approved.message_text, "✓ Looks good"
    )
    return approved


async def _guide_turn(session: ReviewGuideSession, approve_ts: str = "") -> None:
    await dispatch_guide_run(session, prompt("review-guide/looks-good"), approve_ts=approve_ts)


async def advance(channel_id: str, message_ts: str) -> None:
    """Handle a click on "Looks good" on the message at ``message_ts``."""
    session = await ReviewGuideSession.for_channel(channel_id)
    if session is None or session.closed:
        logger.info(
            "Ignoring Looks good in a closed review guide", extra={"slack_channel": channel_id}
        )
        return
    client = langgraph_client()
    runs = await _active_runs(client, session.thread_id)
    # A turn in flight owns the walkthrough, and a pause waits for the pull request's new head.
    if session.paused_message_ts or any(run.metadata.kind != PREFETCH_KIND for run in runs):
        await _guide_turn(session, approve_ts=message_ts)
        return
    await cancel_prefetch(client, session.thread_id)
    session = await ReviewGuideSession.get(session.thread_id)
    walk = session.walk if session is not None else None
    if session is None or walk is None:
        return
    approved = await approve_click(session, walk, message_ts)
    if approved is None:
        logger.info(
            "Ignoring Looks good on a message no longer on screen",
            extra={"agent_thread_id": session.thread_id},
        )
        return
    pr = session.pull_request
    head = await fetch_head(pr.owner, pr.repo, pr.number)
    upcoming = walk.queue[0] if walk.queue else None
    if upcoming is None or head is None or head.head.sha != walk.head_sha:
        await refresh_progress(session, walk)
        await _guide_turn(session)
        return
    posted_ts = await post_with_buttons(session, upcoming.message_text, [LOOKS_GOOD])
    if not posted_ts:
        await _guide_turn(session)
        return
    upcoming.status = "shown"
    upcoming.message_ts = posted_ts
    await session.save_walk(walk)
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
        session, prompt("review-guide/prefetch", depth=QUEUE_DEPTH), prefetch=True
    )


async def top_up_after_run(thread_id: str, kind: str) -> None:
    """After a normal guide turn, get the next chunks ready in the background."""
    if kind == PREFETCH_KIND:
        return
    session = await ReviewGuideSession.get(thread_id)
    if session is not None:
        await start_prefetch(session)
