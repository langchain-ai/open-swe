"""Slack native Stop handling."""

import logging
from collections.abc import Mapping
from functools import partial
from typing import Any

from fastapi import HTTPException
from langgraph_sdk import get_client

from openswe.config import ENV
from openswe.slack.events import claim_slack_event
from openswe.slack.records import lookup_slack_thread_id
from openswe.source_context import SourceContext

logger = logging.getLogger(__name__)

LANGGRAPH_URL = ENV.LANGGRAPH_URL.get()


def _mapping_value(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _thread_metadata(thread: object) -> dict[str, Any]:
    metadata = _mapping_value(thread, "metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


async def _process_agent_session_stopped(
    event: dict[str, object], event_id: str, team_id: str
) -> None:
    channel_id = event.get("channel") or event.get("channel_id")
    thread_ts = event.get("thread_ts")
    if not isinstance(channel_id, str) or not channel_id:
        return
    if not isinstance(thread_ts, str) or not thread_ts or thread_ts == "0":
        return
    client = get_client(url=LANGGRAPH_URL)
    thread_id = await lookup_slack_thread_id(channel_id, thread_ts)
    if not thread_id:
        return
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.debug("Ignoring session stop for unknown thread %s", thread_id)
        return
    slack_thread = SourceContext.from_metadata(_thread_metadata(thread)).slack_thread
    if slack_thread is None or not slack_thread.is_at(channel_id, thread_ts):
        logger.warning(
            "Ignoring session stop with mismatched thread metadata",
            extra={"agent_thread_id": thread_id},
        )
        return
    from openswe.slack.client import post_slack_thread_reply
    from openswe.slack.http import SlackClient, slack_identity
    from openswe.slack.thinking import release_slack_location_status
    from openswe.slack.webhook import SlackAuthorization
    from openswe.threads.handlers import cancel_dashboard_thread

    user_id = event.get("user")
    if not event_id or not team_id:
        logger.warning("Ignoring session stop without an event or Slack workspace")
        return
    expected_team_id = slack_thread.team_id
    if not expected_team_id:
        async with SlackClient.bot() as slack:
            expected_team_id = (await slack_identity(slack))["team_id"]
    if expected_team_id != team_id:
        logger.warning("Ignoring session stop from a different Slack workspace")
        return
    if not isinstance(user_id, str) or not user_id:
        return
    authorization = await SlackAuthorization.resolve(user_id)
    if authorization.identity is None:
        logger.warning("Ignoring session stop from an unlinked Slack user")
        return
    if not await claim_slack_event(event_id):
        return
    try:
        await cancel_dashboard_thread(
            thread_id,
            authorization.subject_for(_thread_metadata(thread)),
            on_interrupted=partial(release_slack_location_status, channel_id, thread_ts),
        )
    except Exception as exc:
        if isinstance(exc, HTTPException) and exc.status_code < 500:
            raise
        await post_slack_thread_reply(
            channel_id,
            thread_ts,
            "I couldn't stop this thread. Please try Stop again.",
            agent_thread_id=thread_id,
        )
        raise
    return


async def process_agent_session_stopped(
    event: dict[str, object], event_id: str = "", team_id: str = ""
) -> None:
    """Stop work immediately when Slack signals the session was stopped."""
    try:
        await _process_agent_session_stopped(event, event_id, team_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to stop Open SWE from a Slack session stop event")
