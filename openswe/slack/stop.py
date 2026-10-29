"""Slack native Stop handling."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import partial
from typing import Any

from fastapi import HTTPException
from langgraph_sdk import get_client
from langgraph_sdk.client import LangGraphClient

from openswe.config import ENV
from openswe.message_queue import QueuedMessage
from openswe.slack.code_channels import CODE_CHANNEL_SESSION_TS, set_session_status
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


async def _active_run_ids(client: LangGraphClient, thread_id: str) -> list[str]:
    run_ids: set[str] = set()
    for status in ("pending", "running"):
        offset = 0
        while True:
            runs = await client.runs.list(thread_id, status=status, limit=100, offset=offset)
            for run in runs:
                run_id = _mapping_value(run, "run_id") or _mapping_value(run, "id")
                if isinstance(run_id, str) and run_id:
                    run_ids.add(run_id)
            if len(runs) < 100:
                break
            offset += len(runs)
    return sorted(run_ids)


async def _process_agent_session_stopped(
    event: dict[str, object], event_id: str, team_id: str
) -> None:
    channel_id = event.get("channel") or event.get("channel_id")
    thread_ts = event.get("thread_ts", CODE_CHANNEL_SESSION_TS)
    if not isinstance(channel_id, str) or not channel_id:
        return
    if not isinstance(thread_ts, str) or not thread_ts:
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
    if thread_ts != CODE_CHANNEL_SESSION_TS:
        from openswe.slack.client import post_slack_thread_reply
        from openswe.slack.http import SlackClient, slack_identity
        from openswe.slack.thinking import release_slack_location_status
        from openswe.threads.handlers import cancel_dashboard_thread
        from openswe.users.models import User

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
        user = await User.for_identity("slack", user_id)
        if user is None or not user.github_login:
            logger.warning("Ignoring session stop from an unlinked Slack user")
            return
        if not await claim_slack_event(event_id):
            return
        try:
            await cancel_dashboard_thread(
                thread_id,
                user.github_login,
                email=user.email,
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
    if event_id and not await claim_slack_event(event_id):
        return

    run_ids = await _active_run_ids(client, thread_id)
    if run_ids:
        await client.runs.cancel_many(thread_id=thread_id, run_ids=run_ids, action="interrupt")
    await QueuedMessage.clear(thread_id)
    await client.threads.update(
        thread_id=thread_id,
        metadata={
            "latest_run_status": "interrupted",
            "stop_requested_at_ms": int(datetime.now(UTC).timestamp() * 1000),
        },
    )
    await set_session_status(channel_id, "active")


async def process_agent_session_stopped(
    event: dict[str, object], event_id: str = "", team_id: str = ""
) -> None:
    """Stop work immediately when Slack signals the session was stopped."""
    try:
        await _process_agent_session_stopped(event, event_id, team_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to stop Open SWE from a Slack session stop event")
