"""Branch a Slack conversation off its agent thread: `/btw`, `/breakout <text>`, `/breakout:web`.

A branch starts from what the agent knows, not only from what people wrote in Slack:
the new thread is a LangGraph fork of the conversation's thread (Agent Server's
``threads/{id}/copy``), with its history, and the Slack messages it never got are
added to its first turn as the Slack thread's context. The original thread, and the
work running on it, are left alone.

The fork belongs to the requester and starts clean: every piece of metadata it
inherits is cleared except what MDA keys its owner and thread record by, so it gets
a sandbox of its own and no conversation finds it but the one the branch names. It
forks only a thread MDA's Slack channel holds, and only for someone who may read it:
a private thread's owner. Otherwise, or when forking fails, the branch is seeded from
the Slack thread as before (:func:`fork_thread` answers ``None``).
"""

import logging
from collections.abc import Mapping
from typing import Final

import httpx
from pydantic import JsonValue

from openswe.dispatch import channel_of
from openswe.input_messages import input_message_timestamps
from openswe.threads import index
from openswe.threads.runtimes import RUNTIMES
from openswe.threads.summary import thread_is_readable

logger = logging.getLogger(__name__)

#: On a fork: the message that branched it, ``<channel>:<message_ts>``, so a call sent again
#: finds the fork it made.
FORK_KEY: Final = "openswe_fork"
#: On a fork: the thread it was forked from.
FORK_OF_KEY: Final = "openswe_fork_of"
#: What a fork keeps of the original's metadata: MDA's owner and thread record, and the
#: graph Agent Server runs it with.
_KEPT_KEYS: Final = frozenset({"owner", "ls_mda_thread", "graph_id", "assistant_id"})


def branch_key(channel_id: str, message_ts: str) -> str:
    """What names the branch a Slack message starts, on its fork."""
    return f"{channel_id}:{message_ts}"


async def fork_thread(source_thread_id: str | None, *, key: str, login: str | None) -> str | None:
    """A fork of ``source_thread_id`` for the branch the message ``key`` starts, or ``None``
    to seed it from Slack instead."""
    from openswe.slack.mda_forwarding import CHANNEL  # noqa: PLC0415 - it imports `/btw`'s

    if not source_thread_id:
        return None
    source = await index.get(source_thread_id)
    if (
        source is None
        or channel_of(source.metadata) != CHANNEL
        or not thread_is_readable(source.metadata, login)
    ):
        return None
    fields = {"agent_thread_id": source_thread_id, "slack_fork_key": key}
    try:
        return await _found(source_thread_id, key) or await _fork(source_thread_id, key)
    except Exception as exc:  # noqa: BLE001 - the branch is seeded from Slack instead
        logger.warning(
            "Could not fork a Slack conversation's thread; seeding the branch from Slack",
            extra={**fields, "error_type": type(exc).__name__},
        )
        return None


async def find_fork(source_thread_id: str, key: str) -> str | None:
    """The fork of ``source_thread_id`` the message ``key`` made, if any; ``None`` when MDA
    can't say."""
    try:
        return await _found(source_thread_id, key)
    except httpx.HTTPError as exc:
        logger.warning(
            "Could not look up a Slack branch's fork",
            extra={"agent_thread_id": source_thread_id, "error_type": type(exc).__name__},
        )
        return None


async def held_timestamps(thread_id: str) -> set[str]:
    """The Slack messages a thread already holds, by timestamp; none for a thread no run
    started on, or when MDA can't say."""
    try:
        response = await RUNTIMES.mda_runtime.read_http.get(f"/threads/{thread_id}/state")
        if response.status_code == httpx.codes.NOT_FOUND:
            return set()
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "Could not read a Slack branch's thread state; its Slack context may repeat",
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


async def _found(source_thread_id: str, key: str) -> str | None:
    response = await RUNTIMES.mda_runtime.read_http.post(
        "/threads/search",
        json={"metadata": {FORK_KEY: key, FORK_OF_KEY: source_thread_id}, "limit": 1},
    )
    response.raise_for_status()
    found = response.json()
    thread_id = found[0].get("thread_id") if isinstance(found, list) and found else None
    return thread_id if isinstance(thread_id, str) and thread_id else None


async def _fork(source_thread_id: str, key: str) -> str:
    """Copy the thread as its conversation, then clear what the copy must not inherit."""
    runtime = RUNTIMES.mda_runtime
    write = await runtime.channel_write(source_thread_id)
    copied = await runtime.http.post(
        f"/threads/{source_thread_id}/copy", headers=dict(write.headers)
    )
    copied.raise_for_status()
    thread = copied.json()
    thread_id = thread.get("thread_id") if isinstance(thread, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        raise httpx.HTTPError("Agent Server's copy named no thread")
    inherited = thread.get("metadata") if isinstance(thread, dict) else None
    metadata: dict[str, JsonValue] = {
        name: None for name in _inherited(inherited) if name not in _KEPT_KEYS
    }
    metadata |= {FORK_KEY: key, FORK_OF_KEY: source_thread_id}
    updated = await runtime.http.patch(
        f"/threads/{thread_id}", json={"metadata": metadata}, headers=dict(write.headers)
    )
    updated.raise_for_status()
    logger.info(
        "Forked a Slack conversation's thread for a branch",
        extra={"agent_thread_id": source_thread_id, "forked_thread_id": thread_id},
    )
    return thread_id


def _inherited(metadata: object) -> list[str]:
    return list(metadata) if isinstance(metadata, Mapping) else []
