"""Hand the Slack conversations that belong to MDA to MDA's Slack channel.

Open SWE's Slack app keeps its URLs at the backend's ``/webhooks/slack``. A
conversation belongs to MDA when its first message qualifies for the canary
(:class:`Canary`): the backend binds the Slack thread to the thread MDA's channel
will use, indexes that thread as the channel's, remembers the request it gated,
and forwards Slack's request to LangSmith Trigger Server exactly as Slack sent it.
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
from dataclasses import dataclass
from typing import Final, Literal, cast, get_args

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

Scope = Literal["off", "users", "dms", "all"]


@dataclass(frozen=True)
class Canary:
    """Which new Slack conversations go to MDA, from the environment's settings.

    ``off`` sends none (the rollback); ``users`` sends those started by the Slack
    users in ``OPEN_SWE_SLACK_MDA_USERS``; ``dms`` adds every DM; ``all`` sends
    every conversation that qualifies at all. A conversation keeps whichever side
    its first message went to.
    """

    scope: Scope
    users: frozenset[str]
    events_url: str

    @classmethod
    def from_env(cls) -> Canary:
        raw = ENV.OPEN_SWE_SLACK_MDA_SCOPE.get().strip().lower()
        scope = cast(Scope, raw) if raw in get_args(Scope) else "off"
        users = frozenset(
            u.strip() for u in ENV.OPEN_SWE_SLACK_MDA_USERS.get().split(",") if u.strip()
        )
        return cls(
            scope=scope, users=users, events_url=ENV.OPEN_SWE_SLACK_TRIGGER_EVENTS_URL.get().strip()
        )

    def takes(self, *, user_id: str, direct_message: bool) -> bool:
        """Whether a new conversation started by ``user_id`` goes to MDA."""
        if self.scope == "off" or not self.events_url:
            return False
        if user_id in self.users:
            return True
        return self.scope == "all" or (self.scope == "dms" and direct_message)


def mda_thread_id(app_id: str, team_id: str, channel_id: str, root_ts: str) -> str:
    """The thread MDA's Slack channel keeps for a Slack thread, as Trigger Server keys it."""
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"slack:v1:{app_id}:{team_id}:{channel_id}:{root_ts}")
    )


async def owned_thread(channel_id: str, root_ts: str) -> str | None:
    """The MDA channel's thread for a Slack thread, or ``None`` when the backend owns it."""
    if not Canary.from_env().events_url:
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
    user_id: str,
    direct_message: bool,
    eligible: bool,
) -> str | None:
    """MDA's thread for a Slack message addressed to Open SWE, or ``None`` to keep it on the backend.

    A Slack thread MDA already owns stays MDA's. A new conversation goes to MDA when it is
    ``eligible`` (not a code channel, concierge DM, bot or breakout) and the canary takes it;
    one the backend already holds never moves.
    """
    canary = Canary.from_env()
    if not canary.events_url:
        # Nothing can be forwarded here, so nothing is MDA's: no lookups either.
        return None
    owned = await owned_thread(channel_id, root_ts)
    if owned is not None:
        return owned
    if not eligible or not canary.takes(user_id=user_id, direct_message=direct_message):
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
