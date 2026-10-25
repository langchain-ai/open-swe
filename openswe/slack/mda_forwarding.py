"""Hand the Slack conversations that belong to MDA to MDA's Slack channel.

Open SWE's Slack app keeps its URLs at the backend's ``/webhooks/slack``. A
conversation belongs to MDA when MDA's Slack channel can start it
(:func:`mda_can_start`): the backend binds the Slack thread to the thread MDA's
channel will use, indexes that thread as the channel's, remembers the request it
gated, and forwards Slack's request to LangSmith Trigger Server exactly as Slack sent it.
Trigger Server verifies Slack's signature as if Slack had called it and hands the
message to the deployment's Slack channel, which asks ``/slack/channel/events``
what to start (:mod:`openswe.slack.channel`). Later messages in that Slack thread,
and Slack's Stop, follow it there. Every other conversation stays on today's intake.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Final

import httpx2
from pydantic import JsonValue

from openswe.config import ENV
from openswe.dispatch import channel_of
from openswe.run_config import Repo
from openswe.slack.records import (
    SlackThreadMappingError,
    bind_slack_thread_id,
    lookup_slack_thread_id,
)
from openswe.slack.request import SlackRequest
from openswe.threads import index
from openswe.threads.router import adopt_channel_run
from openswe.utils.http_pool import pooled_transport
from openswe.webhooks.common import SlackRepoResolution

logger = logging.getLogger(__name__)

CHANNEL: Final = "slack"
#: Slack's headers Trigger Server verifies the request with; nothing else is forwarded.
#: Slack's retry headers in particular make Trigger Server drop a request.
FORWARDED_HEADERS: Final = ("X-Slack-Signature", "X-Slack-Request-Timestamp", "Content-Type")
#: Trigger Server accepts a Slack signature this long after its timestamp.
SIGNATURE_WINDOW_SECONDS: Final = 300
_ATTEMPTS: Final = 5
_TIMEOUT_SECONDS: Final = 10.0
_FIRST_BACKOFF_SECONDS: Final = 1.0
#: Thread metadata key holding the requests the backend gated, until the channel asks for them.
PENDING_KEY: Final = "mda_slack_requests"
_PENDING_TTL_SECONDS: Final = 600

#: The Slack message subtypes Trigger Server starts runs for: plain messages and uploads.
#: It ignores every other (``thread_broadcast``, edits, joins), so none of those is forwarded.
FORWARDED_SUBTYPES: Final = frozenset({"", "file_share"})


def events_url() -> str:
    """Trigger Server's events URL for this environment's Slack app; empty where MDA isn't set up."""
    return ENV.OPEN_SWE_SLACK_TRIGGER_EVENTS_URL.get().strip()


def trigger_server_takes(subtype: str) -> bool:
    """Whether Trigger Server hands a Slack message of this subtype to MDA's channel."""
    return subtype in FORWARDED_SUBTYPES


def mda_can_start(
    *,
    subtype: str,
    from_bot: bool,
    code_channel: bool,
    concierge_dm: bool,
    kitchen_channel: bool,
    breakout: bool,
) -> bool:
    """Whether MDA's Slack channel can start a new conversation with this message.

    It can for a person's mention of Open SWE in a channel and for a message in their
    DM with it: Trigger Server delivers those as ``mention`` and ``directMessage``, and
    the backend's endpoint builds the run it would have started itself. The rest
    needs the backend:

    - A code channel is one session for the whole channel, and a concierge DM one for
      the whole DM; MDA keys a conversation by its Slack thread.
    - A kitchen channel starts work from untagged messages, routed to its workspace.
    - Bots' messages carry the triggering bot through the backend's own intake.
    - Breakouts open flows of their own.
    - Trigger Server ignores subtypes outside :data:`FORWARDED_SUBTYPES`.
    """
    return trigger_server_takes(subtype) and not (
        from_bot or code_channel or concierge_dm or kitchen_channel or breakout
    )


def mda_thread_id(app_id: str, team_id: str, channel_id: str, root_ts: str) -> str:
    """The thread MDA's Slack channel keeps for a Slack thread, as Trigger Server keys it."""
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"slack:v1:{app_id}:{team_id}:{channel_id}:{root_ts}")
    )


async def owned_thread(channel_id: str, root_ts: str) -> str | None:
    """The MDA channel's thread for a Slack thread, or ``None`` when the backend owns it."""
    if not events_url():
        return None
    thread_id = await lookup_slack_thread_id(channel_id, root_ts)
    if thread_id is None:
        return None
    thread = await index.get(thread_id)
    return thread_id if thread is not None and channel_of(thread.metadata) == CHANNEL else None


async def claim_conversation(
    app_id: str, team_id: str, channel_id: str, root_ts: str
) -> str | None:
    """Make a new Slack conversation MDA's: its thread id, or ``None`` if another thread holds it."""
    if not (app_id and team_id and channel_id and root_ts):
        return None
    thread_id = mda_thread_id(app_id, team_id, channel_id, root_ts)
    try:
        await bind_slack_thread_id(channel_id, root_ts, thread_id)
    except SlackThreadMappingError:
        return None
    await adopt_channel_run(thread_id, CHANNEL)
    return thread_id


async def conversation_thread(
    *,
    app_id: str,
    team_id: str,
    channel_id: str,
    root_ts: str,
    can_start: bool,
) -> str | None:
    """MDA's thread for a Slack message addressed to Open SWE, or ``None`` to keep it on the backend.

    A Slack thread MDA already owns stays MDA's. A new conversation goes to MDA when MDA's
    channel ``can_start`` it (:func:`mda_can_start`); one the backend already holds never moves.
    """
    if not events_url():
        # Nothing can be forwarded here, so nothing is MDA's: no lookups either.
        return None
    owned = await owned_thread(channel_id, root_ts)
    if owned is not None:
        return owned
    if not can_start:
        return None
    if await lookup_slack_thread_id(channel_id, root_ts) is not None:
        return None
    return await claim_conversation(app_id, team_id, channel_id, root_ts)


async def remember(
    request: SlackRequest, repo: SlackRepoResolution | None, message_ts: str
) -> None:
    """Keep the request the backend gated, until the channel asks what the message starts."""
    if request.thread_id is None:
        return
    thread = await index.get(request.thread_id)
    pending = _pending(thread.metadata if thread else {})
    pending[message_ts] = {
        "request": request.model_dump(mode="json"),
        "repo": None
        if repo is None or repo.repo is None
        else {**repo.repo.model_dump(mode="json"), "explicit": repo.explicit},
        "at": time.time(),
    }
    await index.update(request.thread_id, {PENDING_KEY: pending})


async def recall(
    thread_id: str, message_ts: str
) -> tuple[SlackRequest, SlackRepoResolution | None] | None:
    """The request remembered for ``message_ts`` in ``thread_id``, taken out of the thread."""
    thread = await index.get(thread_id)
    pending = _pending(thread.metadata if thread else {})
    entry = pending.pop(message_ts, None)
    if not isinstance(entry, dict):
        return None
    await index.update(thread_id, {PENDING_KEY: pending})
    request = SlackRequest.model_validate(entry.get("request"))
    raw_repo = entry.get("repo")
    repo = None
    if isinstance(raw_repo, dict):
        explicit = bool(raw_repo.pop("explicit", False))
        repo = SlackRepoResolution(repo=Repo.model_validate(raw_repo), explicit=explicit)
    return request, repo


def _pending(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The thread's remembered requests that are still recent enough to be asked for."""
    raw = metadata.get(PENDING_KEY)
    cutoff = time.time() - _PENDING_TTL_SECONDS
    pending: dict[str, JsonValue] = {}
    for ts, entry in raw.items() if isinstance(raw, dict) else ():
        at = entry.get("at") if isinstance(entry, dict) else None
        if isinstance(at, int | float) and at >= cutoff:
            pending[ts] = entry
    return pending


def forwarded_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The headers of Slack's request Trigger Server verifies, by their canonical names."""
    return {name: value for name in FORWARDED_HEADERS if (value := headers.get(name))}


async def forward_to_trigger_server(url: str, body: bytes, headers: Mapping[str, str]) -> None:
    """Deliver a Slack request to Trigger Server unchanged, retrying while its signature is valid."""
    sent_at = float(headers.get("X-Slack-Request-Timestamp") or 0)
    backoff = _FIRST_BACKOFF_SECONDS
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = await client.post(url, content=body, headers=dict(headers))
            except httpx2.TransportError:
                logger.warning(
                    "Forwarding a Slack request to Trigger Server failed",
                    extra={"attempt": attempt},
                    exc_info=True,
                )
            else:
                if response.status_code < 500:
                    if not response.is_success:
                        logger.error(
                            "Trigger Server refused a forwarded Slack request",
                            extra={"status": response.status_code},
                        )
                    return
                logger.warning(
                    "Trigger Server could not take a forwarded Slack request yet",
                    extra={"status": response.status_code, "attempt": attempt},
                )
            if attempt == _ATTEMPTS or time.time() + backoff >= sent_at + SIGNATURE_WINDOW_SECONDS:
                break
            await asyncio.sleep(backoff)
            backoff *= 2
    logger.error("Gave up forwarding a Slack request to Trigger Server")
