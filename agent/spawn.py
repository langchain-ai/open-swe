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
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from langgraph_sdk.client import LangGraphClient

from agent.dispatch import ContentBlocks, dispatch_agent_run
from agent.source_context import SlackSurface, SlackThreadRef, SourceContext
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.slack import (
    bind_slack_thread_id,
    delete_slack_thread_associations,
    store_slack_run_mapping,
)

logger = logging.getLogger(__name__)

#: Configurable keys a spawned session inherits from the session that spawned it.
_INHERITED_CONFIG_KEYS = ("user_email", "github_login", "agent_model_id", "agent_effort")


@dataclass(frozen=True)
class SpawnDestination:
    """The Slack location the new session will live at, already created."""

    channel_id: str
    thread_ts: str
    surface: SlackSurface = "slack_thread"


@dataclass(frozen=True)
class SpawnOrigin:
    """The session doing the spawning."""

    thread_id: str
    slack_thread: SlackThreadRef
    configurable: Mapping[str, Any]

    @classmethod
    def from_config(
        cls, configurable: Mapping[str, Any], active_slack: Mapping[str, Any] | SlackThreadRef
    ) -> "SpawnOrigin":
        thread_id = configurable.get("thread_id")
        return cls(
            thread_id=thread_id if isinstance(thread_id, str) else "",
            slack_thread=SlackThreadRef.parse(active_slack) or SlackThreadRef(),
            configurable=configurable,
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
    metadata: dict[str, Any] = {
        "source": "slack",
        "title": handoff.title[:80],
        "source_context": SourceContext.parse(
            {"slack_thread": location.dump(), **handoff.source_context}
        ).dump(),
    }
    if handoff.repo:
        metadata["repo"] = handoff.repo
        metadata["repo_owner"] = handoff.repo["owner"]
        metadata["repo_name"] = handoff.repo["name"]
    github_login = origin.configurable.get("github_login")
    if isinstance(github_login, str) and github_login:
        metadata["github_login"] = github_login
    user_email = origin.configurable.get("user_email")
    if isinstance(user_email, str) and user_email:
        metadata["triggering_user_email"] = user_email.strip().lower()
    return metadata


def _configurable(location: SlackThreadRef, origin: SpawnOrigin, handoff: SpawnHandoff) -> dict:
    configurable: dict[str, Any] = {"slack_thread": location.dump(), "source": "slack"}
    if handoff.repo:
        configurable["repo"] = handoff.repo
    for key in _INHERITED_CONFIG_KEYS:
        value = origin.configurable.get(key)
        if value:
            configurable[key] = value
    return configurable


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
