"""The reader's "Looks good": approve what is on screen, then show what comes next.

Only the button approves. A click approves what it was clicked on right away,
then posts the reader's next planned chunk, or Other once every chunk is done,
with no model turn in between. The guide gets a turn only when the plan has
nothing ready for the reader yet, when the walkthrough is over, or when a turn
is already running; a click that lands during a turn is recorded when the next
one begins.
"""

import logging
from collections import Counter

from openswe.prompts import prompt
from openswe.review_guide.buttons import LOOKS_GOOD
from openswe.review_guide.github import fetch_head
from openswe.review_guide.launch import dispatch_guide_run
from openswe.review_guide.messages import post_with_buttons, refresh_progress, retire
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.review_guide.walk import Approval, OnScreen, Reader, Walk
from openswe.utils.thread_ops import langgraph_client
from openswe.walkthrough.diff import line_key
from openswe.walkthrough.record import Walkthrough

logger = logging.getLogger(__name__)


async def _turn_running(thread_id: str) -> bool:
    client = langgraph_client()
    for status in ("running", "pending"):
        if await client.runs.list(thread_id, status=status, limit=1):
            return True
    return False


async def approve_click(
    session: ReviewGuideSession, walk: Walk, message_ts: str
) -> Approval | None:
    """Record "Looks good" on ``message_ts`` if it is still on screen; returns what it approved."""
    shown = walk.on_screen
    approval = walk.approve(message_ts)
    if approval is None or shown is None:
        return None
    await session.mark_seen(
        Counter(line_key(ref.path, ref.sign, ref.text) for ref in approval.lines)
    )
    await session.save_walk(walk)
    await retire(session.slack_channel_id, shown.message_ts, shown.message_text, "✓ Looks good")
    return approval


async def _guide_turn(session: ReviewGuideSession, approve_ts: str = "") -> None:
    await dispatch_guide_run(session, prompt("review-guide/looks-good"), approve_ts=approve_ts)


async def _post(session: ReviewGuideSession, walk: Walk, shown: OnScreen) -> bool:
    message_ts = await post_with_buttons(session, shown.message_text, [LOOKS_GOOD])
    if not message_ts:
        return False
    shown.message_ts = message_ts
    walk.on_screen = shown
    await session.save_walk(walk)
    return True


async def advance(channel_id: str, message_ts: str) -> None:
    """Handle a click on "Looks good" on the message at ``message_ts``."""
    session = await ReviewGuideSession.for_channel(channel_id)
    if session is None or session.closed:
        logger.info(
            "Ignoring Looks good in a closed review guide", extra={"slack_channel": channel_id}
        )
        return
    # A turn in flight owns the walk, and a pause waits for the pull request's new head.
    if session.paused_message_ts or await _turn_running(session.thread_id):
        await _guide_turn(session, approve_ts=message_ts)
        return
    walk = session.walk
    if walk is None or await approve_click(session, walk, message_ts) is None:
        logger.info(
            "Ignoring Looks good on a message no longer on screen",
            extra={"agent_thread_id": session.thread_id},
        )
        return
    pr = session.pull_request
    head = await fetch_head(pr.owner, pr.repo, pr.number)
    stored = await Walkthrough.get(pr.id)
    if (
        head is None
        or stored is None
        or head.head.sha != walk.head_sha
        or stored.head_sha != walk.head_sha
    ):
        await _guide_turn(session)
        return
    reader = Reader.of(walk, stored.plan, await session.seen_lines())
    upcoming = reader.next_chunk()
    if upcoming is not None:
        shown = reader.show(upcoming)
    elif stored.complete and reader.other_pending:
        shown = reader.show_other()
    else:
        await refresh_progress(session, reader)
        await _guide_turn(session)
        return
    if not await _post(session, walk, shown):
        await _guide_turn(session)
        return
    await refresh_progress(session, reader)
    logger.info(
        "Review guide showed the next planned chunk",
        extra={"agent_thread_id": session.thread_id, "guide_chunk": shown.chunk_id},
    )
