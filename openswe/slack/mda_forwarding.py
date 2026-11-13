"""The Slack conversations MDA's Slack channel holds.

Robin's Slack app delivers its events to LangSmith Trigger Server, which hands each
message to the MDA deployment's Slack channel. The channel decides what a message
starts and makes a new conversation its own (:mod:`openswe.slack.channel`); later
messages in that Slack thread, Slack's Stop, and clicks on the agent's buttons there
follow it. It hands back what the backend answers itself.

This module keeps which thread holds a Slack conversation. A request for one of
MDA's conversations that reaches the backend anyway starts nothing
(``SlackEventsDelivery.held_by_mda``).
"""

import logging
import uuid
from typing import Final, Literal

from pydantic import JsonValue

from openswe.dispatch import channel_of
from openswe.slack.client import SlackThreadMappingError
from openswe.slack.dm import CONCIERGE_TS
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.records import bind_slack_thread_id, lookup_slack_thread_id
from openswe.threads import index
from openswe.threads.router import adopt_channel_run
from openswe.threads.runtimes import RUNTIMES
from openswe.webhooks.common import (
    PROVISIONAL_VISIBILITY_KEY,
)

logger = logging.getLogger(__name__)

CHANNEL: Final = "slack"
#: The job that once reported forwarded messages the channel never asked about; nothing is
#: remembered for it any more, so its schedule is cancelled.
RETIRED_SWEEP_JOB: Final = "mda_slack_lost_requests"

#: The Slack message subtypes Trigger Server starts runs for: plain messages and uploads.
#: It ignores every other (``thread_broadcast``, edits, joins).
FORWARDED_SUBTYPES: Final = frozenset({"", "file_share"})


def trigger_server_takes(subtype: str) -> bool:
    """Whether Trigger Server hands a Slack message of this subtype to MDA's channel."""
    return subtype in FORWARDED_SUBTYPES


def mda_thread_id(app_id: str, team_id: str, channel_id: str, root_ts: str) -> str:
    """The thread MDA's Slack channel keeps for a Slack thread, as Trigger Server keys it."""
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"slack:v1:{app_id}:{team_id}:{channel_id}:{root_ts}")
    )


async def owned_thread(channel_id: str, root_ts: str) -> str | None:
    """The MDA channel's thread for a Slack thread, or ``None`` when the backend owns it."""
    thread_id = await lookup_slack_thread_id(channel_id, root_ts)
    if thread_id is None:
        return None
    thread = await index.get(thread_id)
    return thread_id if thread is not None and channel_of(thread.metadata) == CHANNEL else None


async def owned_conversation(channel_id: str, thread_ts: str) -> str | None:
    """The MDA channel's thread a click in this Slack thread belongs to, or ``None``.

    A concierge DM's unthreaded buttons belong to the DM's one thread.
    """
    if thread_id := await owned_thread(channel_id, thread_ts):
        return thread_id
    if channel_id.startswith("D") and thread_ts != CONCIERGE_TS:
        return await owned_thread(channel_id, CONCIERGE_TS)
    return None


async def claim_conversation(
    app_id: str,
    team_id: str,
    channel_id: str,
    root_ts: str,
    *,
    owner_login: str | None = None,
    channel_context: SlackChannelContext,
) -> str:
    """Claim a Slack conversation with its verified channel visibility from creation."""
    thread_id = mda_thread_id(app_id, team_id, channel_id, root_ts)
    await bind_slack_thread_id(channel_id, root_ts, thread_id)
    collaborative = (
        channel_context.allows_operations
        and channel_context.is_im is not True
        and not channel_id.startswith("D")
    )
    await _adopt(thread_id, owner_login, visibility="public" if collaborative else "private")
    return thread_id


async def _adopt(
    thread_id: str, owner_login: str | None, *, visibility: Literal["private", "public"] = "private"
) -> None:
    metadata: dict[str, JsonValue] = {
        "visibility": visibility,
        PROVISIONAL_VISIBILITY_KEY: True,
        "source": "slack",
    }
    if owner_login:
        metadata["owner_type"] = "user"
        metadata["owner_login"] = owner_login
    await adopt_channel_run(thread_id, CHANNEL, metadata=metadata)


def side_thread_id(channel_id: str, message_ts: str) -> str:
    """The thread a `/btw` question or a new breakout runs on, apart from its Slack thread's.

    Derived from the message, so MDA's Slack channel finds the request remembered there
    when it asks about the message (``plan_slack_turn``).
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:slack-side:{channel_id}:{message_ts}"))


async def claim_side(thread_id: str, *, owner_login: str | None = None) -> None:
    """Make a side thread (:func:`side_thread_id`) MDA's channel's, before its request is
    remembered."""
    await _adopt(thread_id, owner_login)


async def holds(thread_id: str) -> bool:
    """Whether ``thread_id`` is a conversation of MDA's Slack channel."""
    thread = await index.get(thread_id) if thread_id else None
    return thread is not None and channel_of(thread.metadata) == CHANNEL


async def claim_breakout(thread_id: str, *, owner_login: str | None = None) -> None:
    """Make a breakout the agent opens with its tool MDA's channel's: a side thread the
    channel's handlers run from its first reply on, which the backend starts first.

    Made on MDA as the handlers' own thread before the backend starts that run there,
    since the channel never started one.
    """
    await _adopt(thread_id, owner_login)
    await RUNTIMES.mda_runtime.create_handler_thread(thread_id, provider=CHANNEL, channel=CHANNEL)


def concierge_thread_id(app_id: str, team_id: str, channel_id: str) -> str:
    """The one thread an MDA concierge DM runs on."""
    return mda_thread_id(app_id, team_id, channel_id, CONCIERGE_TS)


async def claim_concierge(
    app_id: str, team_id: str, channel_id: str, *, owner_login: str | None = None
) -> str:
    """Make a concierge DM MDA's: its thread id."""
    thread_id = concierge_thread_id(app_id, team_id, channel_id)
    await bind_slack_thread_id(channel_id, CONCIERGE_TS, thread_id)
    await _adopt(thread_id, owner_login)
    return thread_id


async def join_concierge(channel_id: str, root_ts: str, thread_id: str) -> None:
    """Bind a Slack thread in an MDA concierge DM to the DM's thread, so Stop finds it."""
    if root_ts == CONCIERGE_TS:
        return
    try:
        await bind_slack_thread_id(channel_id, root_ts, thread_id)
    except SlackThreadMappingError:
        logger.warning(
            "A Slack thread in a concierge DM belongs to another thread",
            extra={"agent_thread_id": thread_id, "slack_thread_ts": root_ts},
        )


class MdaHandoffLost(RuntimeError):
    """A Slack message in a conversation MDA's channel holds that will never start a run."""
