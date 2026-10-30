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

Every environment forwards: where MDA isn't set up, Trigger Server refuses the request
(an unregistered Slack app gets a 401), and the backend takes a conversation the channel
never started back and answers it itself, as it always has (:func:`take_back`). A
forwarded message never ends in silence: when a conversation MDA's channel started can't
be forwarded, or the channel
never asks about a message (:func:`sweep_lost_requests`), the backend takes the
remembered request back and replies with an error in the Slack thread. Taking it is
atomic, so the person gets the run, the backend's answer or the error, never two.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Final

import httpx2
from pydantic import JsonValue

from openswe.dispatch import CHANNEL_METADATA_KEY, channel_of
from openswe.jobs import queue
from openswe.run_config import Repo
from openswe.slack.records import bind_slack_thread_id, lookup_slack_thread_id
from openswe.slack.request import SlackRequest
from openswe.slack.webhook import notify_slack_processing_error, process_slack_mention
from openswe.threads import index
from openswe.threads.router import adopt_channel_run
from openswe.threads.runtimes import RUNTIMES
from openswe.utils.http_pool import pooled_transport
from openswe.webhooks.common import (
    PROVISIONAL_VISIBILITY_KEY,
    SlackRepoResolution,
    visibility_is_settled,
)

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
#: Thread metadata key holding each request the channel took, with the call that took it,
#: so MDA retrying that call gets the same request again.
RECALLED_KEY: Final = "mda_slack_recalled"
#: MDA retries a call within three attempts of at most a minute each.
_REPLAY_SECONDS: Final = 300
#: A forwarded message the channel hasn't asked about by then is lost: the person hears so.
LOST_AFTER_SECONDS: Final = 90
SWEEP_JOB: Final = "mda_slack_lost_requests"
SWEEP_SCHEDULE: Final = "* * * * *"

#: The Slack message subtypes Trigger Server starts runs for: plain messages and uploads.
#: It ignores every other (``thread_broadcast``, edits, joins), so none of those is forwarded.
FORWARDED_SUBTYPES: Final = frozenset({"", "file_share"})


#: Trigger Server's Slack events URL, the same for every environment: it tells requests
#: apart by the Slack app that signed them.
TRIGGER_EVENTS_URL: Final = (
    "https://api.smith.langchain.com/v1/triggers/webhooks/d809e66e-0000-4000-8000-000000000003"
)


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
    thread_id = await lookup_slack_thread_id(channel_id, root_ts)
    if thread_id is None:
        return None
    thread = await index.get(thread_id)
    return thread_id if thread is not None and channel_of(thread.metadata) == CHANNEL else None


async def channel_started(thread_id: str) -> bool:
    """Whether MDA's channel started a run in the conversation it was handed.

    A claimed conversation's visibility stays provisional until the channel's first run
    writes the thread's metadata, so one still provisional never started anything.
    """
    thread = await index.get(thread_id)
    return thread is None or visibility_is_settled(thread.metadata)


async def claim_conversation(
    app_id: str, team_id: str, channel_id: str, root_ts: str, *, owner_login: str | None = None
) -> str:
    """Make a new Slack conversation MDA's, as :func:`startable_thread` named it: its thread id.

    ``owner_login`` is the GitHub login of the person who started it, who may prompt the
    thread while it is still private. Raises :class:`SlackThreadMappingError` when
    another thread took the Slack thread since.
    """
    thread_id = mda_thread_id(app_id, team_id, channel_id, root_ts)
    await bind_slack_thread_id(channel_id, root_ts, thread_id)
    # Private to the person who started it until the thread's first metadata write, by the
    # channel's run or by the backend taking the conversation back, says who may see it.
    metadata: dict[str, JsonValue] = {
        "visibility": "private",
        PROVISIONAL_VISIBILITY_KEY: True,
        "source": "slack",
    }
    if owner_login:
        metadata["owner_login"] = owner_login
    await adopt_channel_run(thread_id, CHANNEL, metadata=metadata)
    return thread_id


async def startable_thread(
    *,
    app_id: str,
    team_id: str,
    channel_id: str,
    root_ts: str,
    can_start: bool,
) -> str | None:
    """The thread MDA would claim for a new conversation, or ``None`` to keep it on the backend.

    A new conversation goes to MDA when MDA's channel ``can_start`` it
    (:func:`mda_can_start`); one the backend already holds never moves. Nothing is
    claimed yet: the backend gates the message first (:func:`claim_conversation`).
    """
    if not (can_start and app_id and team_id and channel_id and root_ts):
        return None
    if await lookup_slack_thread_id(channel_id, root_ts) is not None:
        return None
    return mda_thread_id(app_id, team_id, channel_id, root_ts)


async def remember(
    request: SlackRequest, repo: SlackRepoResolution | None, message_ts: str
) -> None:
    """Keep the request the backend gated, until the channel asks what the message starts."""
    if request.thread_id is None:
        return
    entry: dict[str, JsonValue] = {
        "request": request.model_dump(mode="json"),
        "repo": None
        if repo is None or repo.repo is None
        else {**repo.repo.model_dump(mode="json"), "explicit": repo.explicit},
        "at": time.time(),
    }
    # One statement, so a message remembered while another is recalled keeps both.
    await index.put_entry(
        request.thread_id,
        PENDING_KEY,
        message_ts,
        entry,
        expired_before=time.time() - _PENDING_TTL_SECONDS,
    )


async def recall(
    thread_id: str, message_ts: str, call_id: str
) -> tuple[SlackRequest, SlackRepoResolution | None] | None:
    """The request remembered for ``message_ts`` in ``thread_id``, taken out of the thread.

    One call takes it. MDA retries a call it got no answer to with the same webhook id,
    ``call_id``, and that call gets the request again, so the run is still built once
    for MDA; any other call gets ``None``.
    """
    now = time.time()
    entry = await index.take_entry(thread_id, PENDING_KEY, message_ts)
    if isinstance(entry, dict):
        taken: dict[str, JsonValue] = {"entry": entry, "by": call_id, "at": now}
        await index.put_entry(
            thread_id, RECALLED_KEY, message_ts, taken, expired_before=now - _REPLAY_SECONDS
        )
    else:
        entry = await _taken_by(thread_id, message_ts, call_id, since=now - _REPLAY_SECONDS)
    if entry is None or not _stamped_since(entry, now - _PENDING_TTL_SECONDS):
        return None
    return _remembered(entry)


async def _taken_by(
    thread_id: str, message_ts: str, call_id: str, *, since: float
) -> dict[str, JsonValue] | None:
    """The request ``call_id`` took for ``message_ts`` since ``since``, if it took one."""
    thread = await index.get(thread_id)
    recalled = thread.metadata.get(RECALLED_KEY) if thread is not None else None
    taken = recalled.get(message_ts) if isinstance(recalled, dict) else None
    if not (isinstance(taken, dict) and taken.get("by") == call_id):
        return None
    entry = taken.get("entry")
    return entry if isinstance(entry, dict) and _stamped_since(taken, since) else None


def _stamped_since(entry: Mapping[str, JsonValue], since: float) -> bool:
    at = entry.get("at")
    return isinstance(at, int | float) and at >= since


def _remembered(
    entry: dict[str, JsonValue],
) -> tuple[SlackRequest, SlackRepoResolution | None]:
    request = SlackRequest.model_validate(entry.get("request"))
    raw_repo = entry.get("repo")
    repo = None
    if isinstance(raw_repo, dict):
        explicit = bool(raw_repo.pop("explicit", False))
        repo = SlackRepoResolution(repo=Repo.model_validate(raw_repo), explicit=explicit)
    return request, repo


def forwarded_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The headers of Slack's request Trigger Server verifies, by their canonical names."""
    return {name: value for name in FORWARDED_HEADERS if (value := headers.get(name))}


async def report_lost(thread_id: str, message_ts: str, why: str) -> bool:
    """Reply in the Slack thread that its message won't run; whether it did.

    Takes the remembered request back first, so a message the channel already asked
    about, and so ran, is left alone, and a message is reported once.
    """
    entry = await index.take_entry(thread_id, PENDING_KEY, message_ts)
    if not isinstance(entry, dict):
        return False
    request, repo = _remembered(entry)
    await notify_slack_processing_error(request, repo.repo if repo else None, MdaHandoffLost(why))
    return True


async def take_back(thread_id: str, message_ts: str) -> bool:
    """Answer a conversation Trigger Server refused on the backend, as before MDA; whether it did.

    Only while the channel never started the conversation (:func:`channel_started`): the
    thread stops being the channel's, so its later messages stay here too, and the backend
    runs the request it gated. Taking the request back first leaves one the channel already
    asked about alone.
    """
    entry = await index.take_entry(thread_id, PENDING_KEY, message_ts)
    if not isinstance(entry, dict):
        return False
    request, repo = _remembered(entry)
    released: dict[str, JsonValue] = {CHANNEL_METADATA_KEY: None}
    await index.update(thread_id, released)
    await RUNTIMES.local.update(thread_id, released)
    await process_slack_mention(request, repo)
    return True


class MdaHandoffLost(RuntimeError):
    """A Slack message forwarded to MDA's channel that will never start a run."""


async def forward_to_trigger_server(
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    thread_id: str,
    message_ts: str,
    event_id: str,
) -> None:
    """Deliver a Slack request to Trigger Server unchanged, retrying while its signature is valid.

    When Trigger Server refuses it, or every attempt fails, the message remembered at
    ``message_ts`` (none for Slack's Stop) is answered here: by the backend itself while
    the channel never started the conversation (:func:`take_back`), else with an error.
    """
    sent_at = float(headers.get("X-Slack-Request-Timestamp") or 0)
    backoff = _FIRST_BACKOFF_SECONDS
    status: int | None = None
    attempt = 0
    async with httpx2.AsyncClient(
        transport=pooled_transport(), timeout=_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                response = await client.post(url, content=body, headers=dict(headers))
            except httpx2.TransportError:
                status = None
                logger.warning(
                    "Forwarding a Slack request to Trigger Server failed",
                    extra={"slack_event_id": event_id, "attempt": attempt},
                    exc_info=True,
                )
            else:
                status = response.status_code
                if status < 500:
                    break
                logger.warning(
                    "Trigger Server could not take a forwarded Slack request yet",
                    extra={"slack_event_id": event_id, "status": status, "attempt": attempt},
                )
            if attempt == _ATTEMPTS or time.time() + backoff >= sent_at + SIGNATURE_WINDOW_SECONDS:
                break
            await asyncio.sleep(backoff)
            backoff *= 2
    # An unfollowed redirect took nothing either.
    delivered = status is not None and 200 <= status < 300
    outcome = "delivered" if delivered else "refused" if status and status < 500 else "gave_up"
    logger.log(
        logging.INFO if delivered else logging.ERROR,
        "Forwarded a Slack request to Trigger Server"
        if delivered
        else "Trigger Server never took a forwarded Slack request",
        extra={
            "slack_event_id": event_id,
            "agent_thread_id": thread_id,
            "slack_message_ts": message_ts,
            "forward_outcome": outcome,
            "status": status,
            "attempts": attempt,
        },
    )
    if not delivered and message_ts:
        if await channel_started(thread_id):
            await report_lost(thread_id, message_ts, f"Trigger Server forward {outcome} ({status})")
        else:
            await take_back(thread_id, message_ts)


async def sweep_lost_requests() -> int:
    """Reply in each Slack thread whose forwarded message the channel never asked about.

    Normally the channel asks within seconds; one that hasn't after
    :data:`LOST_AFTER_SECONDS` was dropped on the way (Trigger Server, MDA's
    deployment, a restart). Entries past the TTL are dropped unreported, and so are
    the requests the channel took once MDA can no longer retry the call.
    """
    now = time.time()
    for thread_id, message_ts in await index.entries_between(
        RECALLED_KEY, after=now - _PENDING_TTL_SECONDS, before=now - _REPLAY_SECONDS
    ):
        await index.take_entry(thread_id, RECALLED_KEY, message_ts)
    reported = 0
    for thread_id, message_ts in await index.entries_between(
        PENDING_KEY, after=now - _PENDING_TTL_SECONDS, before=now - LOST_AFTER_SECONDS
    ):
        try:
            lost = await report_lost(
                thread_id, message_ts, "MDA's Slack channel never asked what the message starts"
            )
        except Exception:
            logger.exception(
                "Could not report a lost Slack message",
                extra={"agent_thread_id": thread_id, "slack_message_ts": message_ts},
            )
            continue
        reported += lost
    return reported


async def schedule_sweep() -> None:
    """Keep :func:`sweep_lost_requests` running every minute."""
    await queue.schedule(SWEEP_JOB, cron=SWEEP_SCHEDULE, key=SWEEP_JOB)
