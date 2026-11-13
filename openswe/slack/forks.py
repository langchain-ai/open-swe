"""Branch a Slack conversation off its agent thread: `/btw`, `/breakout <text>`, `/breakout:web`.

A branch starts from what the agent knows, not only from what people wrote in Slack.
It runs on a new thread of its own (``mda_forwarding.side_thread_id``), which MDA's Slack
channel starts with the conversation's thread state: its history, read by the channel's
handler as the conversation's own runs read it. The Slack messages that thread never got
are added to the branch's first turn as the Slack thread's context. The original thread,
and the work running on it, are left alone.

The new thread is the channel's, so its runs start under the owner MDA gives every
thread a handler chooses; a copy of the conversation's thread would keep the
conversation's owner, and MDA could not start a run on it. It branches only off a thread
MDA's Slack channel holds, and only for someone who may read it: a private thread's
owner. Otherwise the branch is seeded from the Slack thread as before.
"""

import logging

import httpx2

from openswe.dispatch import channel_of
from openswe.input_messages import input_message_timestamps
from openswe.threads import index
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.summary import thread_is_readable

logger = logging.getLogger(__name__)


async def branches_from(source_thread_id: str | None, *, login: str | None) -> bool:
    """Whether a branch may start with what ``source_thread_id`` holds: a thread MDA's
    Slack channel holds, which ``login`` may read."""
    from openswe.slack.mda_forwarding import CHANNEL  # noqa: PLC0415 - it imports `/btw`'s

    if not source_thread_id:
        return False
    source = await index.get(source_thread_id)
    return (
        source is not None
        and channel_of(source.metadata) == CHANNEL
        and thread_is_readable(source.metadata, login)
    )


async def held_timestamps(thread_id: str) -> set[str]:
    """The Slack messages a thread already holds, by timestamp; none for a thread no run
    started on, or when MDA can't say."""
    try:
        async with RUNTIMES.mda_runtime.read_http as client:
            response = await client.get(f"/threads/{thread_id}/state")
        if response.status_code == httpx2.codes.NOT_FOUND:
            return set()
        response.raise_for_status()
    except httpx2.HTTPError as exc:
        logger.warning(
            "Could not read what a Slack branch starts with; its Slack context may repeat",
            extra={"agent_thread_id": thread_id, "error_type": type(exc).__name__},
        )
        return set()
    state = response.json()
    values = state.get("values") if isinstance(state, dict) else None
    messages = values.get("messages") if isinstance(values, dict) else None
    held: set[str] = set()
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, dict):
            held |= input_message_timestamps(message.get("content"))
    return held
