"""Spawning a second agent session from a running one.

A session spawns another when work needs its own place to happen: a breakout
thread, or a code channel. The Slack destination is the caller's to create —
posting a headline message, or asking Slack for a channel — and everything after
that point is the same either way: claim a thread id, bind the location to it,
write the thread's metadata, and start the first run with the context the parent
is handing over.

Each layer undoes what it created. If a spawn fails partway, it detaches the
binding it made and re-raises, so the caller only has to clean up the
destination it created itself.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from langgraph_sdk.client import LangGraphClient

from agent.dispatch import ContentBlocks, dispatch_agent_run
from agent.run_config import RunConfig
from agent.slack.client import (
    bind_slack_thread_id,
    delete_slack_thread_associations,
    invite_to_slack_channel,
    resolve_slack_thread_id,
    slack_user_ids,
    store_slack_run_mapping,
)
from agent.slack.code_channels import (
    CODE_CHANNEL_SESSION_TS,
    archive_code_channel,
    create_code_channel,
)
from agent.slack.surfaces.channel import SlackChannelSurface
from agent.source_context import SlackSurfaceKind, SlackThreadRef, SourceContext
from agent.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)

#: Configurable keys a spawned session inherits from the session that spawned it.
_INHERITED_CONFIG_KEYS = ("user_email", "github_login", "agent_model_id", "agent_effort")


@dataclass(frozen=True)
class SpawnDestination:
    """The Slack location the new session will live at, already created."""

    channel_id: str
    thread_ts: str
    surface: SlackSurfaceKind = "slack_thread"


@dataclass(frozen=True)
class SpawnOrigin:
    """The session doing the spawning."""

    thread_id: str
    slack_thread: SlackThreadRef
    config: RunConfig

    @classmethod
    def from_config(
        cls, cfg: RunConfig, active_slack: Mapping[str, Any] | SlackThreadRef
    ) -> SpawnOrigin:
        return cls(
            thread_id=cfg.thread_id or "",
            slack_thread=SlackThreadRef.parse(active_slack) or SlackThreadRef(),
            config=cfg,
        )


@dataclass(frozen=True)
class SpawnHandoff:
    """What the parent hands to the new session."""

    title: str
    content: ContentBlocks
    repo: dict[str, str] | None = None
    #: Extra `source_context` keys recording where the session came from, such as
    #: `breakout_from` or `spawned_from`.
    source_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpawnedSession:
    thread_id: str
    slack_thread: SlackThreadRef
    run_id: str | None
    dashboard_url: str | None


def _new_location(destination: SpawnDestination, origin: SpawnOrigin) -> SlackThreadRef:
    parent = origin.slack_thread
    return SlackThreadRef(
        channel_id=destination.channel_id,
        thread_ts=destination.thread_ts,
        team_id=parent.team_id,
        surface=destination.surface,
        triggering_user_id=parent.triggering_user_id,
        triggering_user_name=parent.triggering_user_name,
        triggering_user_email=parent.triggering_user_email,
        triggering_user_timezone=parent.triggering_user_timezone,
        triggering_event_ts=destination.thread_ts,
    )


def _metadata(
    location: SlackThreadRef, origin: SpawnOrigin, handoff: SpawnHandoff
) -> dict[str, Any]:
    # The seed says this title is the one it was handed, not one generated from
    # the conversation: title generation replaces a title that still matches its
    # seed, and renames the code channel with it.
    provisional_title = handoff.title[:80]
    metadata: dict[str, Any] = {
        "source": "slack",
        "title": provisional_title,
        "title_seed": provisional_title,
        "source_context": SourceContext.parse(
            {"slack_thread": location.dump(), **handoff.source_context}
        ).dump(),
    }
    if handoff.repo:
        metadata["repo"] = handoff.repo
        metadata["repo_owner"] = handoff.repo["owner"]
        metadata["repo_name"] = handoff.repo["name"]
    if origin.config.github_login:
        metadata["github_login"] = origin.config.github_login
    if origin.config.user_email:
        metadata["triggering_user_email"] = origin.config.user_email.strip().lower()
    return metadata


def _configurable(location: SlackThreadRef, origin: SpawnOrigin, handoff: SpawnHandoff) -> dict:
    configurable: dict[str, Any] = {"slack_thread": location.dump(), "source": "slack"}
    if handoff.repo:
        configurable["repo"] = handoff.repo
    for key in _INHERITED_CONFIG_KEYS:
        value = origin.config.get(key)
        if value:
            configurable[key] = value
    return configurable


def _session_id(origin: SpawnOrigin, title: str, content: ContentBlocks) -> str:
    """Slack's idempotency key for this request to open a channel.

    Derived from the request rather than minted per attempt: Slack can create
    the channel and lose the response, and a retry that asks for the same work
    has to get that channel back instead of a second one. Two genuinely
    different tasks differ in their instructions, so they differ here too.
    """
    request = "\0".join((origin.thread_id, origin.slack_thread.channel_id, title, str(content)))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:code-channel:{request}"))


class CodeChannelError(Exception):
    """A code channel could not be opened, with a message for the caller."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class OpenedCodeChannel:
    channel_id: str
    session: SpawnedSession
    invited: list[str]
    warnings: list[str]


async def open_code_channel(
    client: LangGraphClient,
    *,
    title: str,
    content: ContentBlocks,
    repo: dict[str, str] | None,
    origin: SpawnOrigin,
    invite: Sequence[str],
    source_context: dict[str, Any] | None = None,
    origin_channel_id: str = "",
    origin_message_ts: str = "",
    team_id: str = "",
    is_private: bool = False,
) -> OpenedCodeChannel:
    """Open a code channel, put people in it, and start the session that works there.

    A new channel holds nobody but the bot: joining it is up to the people named
    in `invite`, and a channel nobody is in is a channel nobody reads, so at
    least one person is required. The origin pair is separate and optional, and
    goes in together or not at all: with it Slack inherits the origin channel's
    privacy and lets its members join too.
    """
    invitees = slack_user_ids(invite)
    if not invitees:
        raise CodeChannelError(
            "invite must name at least one Slack user id (for example the person who asked)"
        )
    origin_pair = origin_channel_id and origin_message_ts
    channel_id, error = await create_code_channel(
        name=title,
        session_id=_session_id(origin, title, content),
        origin_channel_id=origin_channel_id if origin_pair else "",
        origin_message_ts=origin_message_ts if origin_pair else "",
        team_id=team_id,
        is_private=is_private,
    )
    if not channel_id:
        raise CodeChannelError(error or "Slack could not create the code channel")

    # Slack lets the origin channel's members into the new one, and every event
    # that produces reaches the Slack webhook, which binds a code channel it
    # finds unbound. Claim the id it derives rather than racing it with a fresh
    # one: whichever of the two gets there first binds the same id.
    try:
        thread_id = await resolve_slack_thread_id(client, channel_id, CODE_CHANNEL_SESSION_TS)
    except Exception as exc:
        with suppress(Exception):
            await archive_code_channel(channel_id)
        raise CodeChannelError(
            f"Could not claim a session for the new code channel: {exc}", retryable=True
        ) from exc

    warnings: list[str] = []
    # People before the work: the channel is where the conversation happens, and
    # nobody sees it start unless they are in it.
    invited_count, invite_error = await invite_to_slack_channel(channel_id, invitees)
    invited = invitees if invited_count else []
    if invite_error:
        warnings.append(f"Could not invite {', '.join(invitees)}: {invite_error}")

    # Chrome before the run, not after: the run's completion is what returns the
    # session to `active`, and a `processing` set after that would stick.
    surface = SlackChannelSurface(channel_id)
    await surface.begin_turn()
    try:
        await surface.start_session(repo=repo, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Could not set up the channel's context and commands: {exc}")

    try:
        session = await spawn_slack_session(
            client,
            destination=SpawnDestination(
                channel_id=channel_id,
                thread_ts=CODE_CHANNEL_SESSION_TS,
                surface="slack_channel",
            ),
            origin=origin,
            handoff=SpawnHandoff(
                title=title,
                content=content,
                repo=repo,
                source_context=source_context or {},
            ),
            thread_id=thread_id,
        )
    except Exception as exc:
        with suppress(Exception):
            await archive_code_channel(channel_id)
        raise CodeChannelError(
            f"Could not start a session in the new code channel: {exc}", retryable=True
        ) from exc
    return OpenedCodeChannel(
        channel_id=channel_id, session=session, invited=list(invited), warnings=warnings
    )


async def spawn_slack_session(
    client: LangGraphClient,
    *,
    destination: SpawnDestination,
    origin: SpawnOrigin,
    handoff: SpawnHandoff,
    thread_id: str = "",
) -> SpawnedSession:
    """Bind a Slack location to a new agent session and start its first run.

    Pass ``thread_id`` when the destination was created with the id already in
    hand — Slack's code-channel creation takes it as an idempotency key.
    """
    thread_id = thread_id or str(uuid.uuid4())
    location = _new_location(destination, origin)
    metadata = _metadata(location, origin, handoff)
    bound = False
    try:
        await bind_slack_thread_id(client, destination.channel_id, destination.thread_ts, thread_id)
        bound = True
        await client.threads.create(thread_id=thread_id, if_exists="do_nothing", metadata=metadata)
        await client.threads.update(thread_id=thread_id, metadata=metadata)
        run = await dispatch_agent_run(
            thread_id,
            handoff.content,
            _configurable(location, origin, handoff),
            source="slack",
            client=client,
        )
    except Exception:
        if bound:
            with suppress(Exception):
                await delete_slack_thread_associations(
                    client,
                    destination.channel_id,
                    destination.thread_ts,
                    expected_thread_id=thread_id,
                )
        raise

    run_id = run.get("run_id") if isinstance(run, Mapping) else None
    run_id = run_id if isinstance(run_id, str) and run_id else None
    if run_id:
        await store_slack_run_mapping(
            client,
            destination.channel_id,
            destination.thread_ts,
            run_id,
            message_ts=destination.thread_ts,
            triggering_user_id=location.triggering_user_id or None,
        )
    return SpawnedSession(
        thread_id=thread_id,
        slack_thread=location,
        run_id=run_id,
        dashboard_url=dashboard_thread_url(thread_id),
    )
