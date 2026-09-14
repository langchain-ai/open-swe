"""Recovery for an agent thread whose Slack thread no longer exists.

Slack does not reject a reply to a deleted parent: it posts it at the channel
root, where it reads as an answer to nothing. Such a thread is moved to the
dashboard so the rest of the conversation has somewhere to go.
"""

import logging
from collections.abc import Mapping

from langgraph_sdk.client import LangGraphClient

from agent.dashboard.threads.summary import DASHBOARD_SOURCE
from agent.slack.client import (
    SLACK_DETACHED_AT_KEY,
    SLACK_DETACHED_FROM_KEY,
    delete_slack_thread_associations,
)
from agent.source_context import SourceContext
from agent.store import now_iso
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)


def slack_thread_detached(metadata: Mapping[str, object] | None) -> bool:
    """Whether this thread has already been moved off Slack."""
    if not isinstance(metadata, Mapping):
        return False
    return bool(metadata.get(SLACK_DETACHED_AT_KEY)) and (
        SourceContext.from_metadata(metadata).slack_thread is None
    )


async def move_thread_to_dashboard(
    langgraph_client: LangGraphClient,
    thread_id: str,
    channel_id: str,
    thread_ts: str,
) -> bool:
    """Detach an agent thread from its Slack thread and keep it in the dashboard."""
    try:
        await delete_slack_thread_associations(
            langgraph_client, channel_id, thread_ts, expected_thread_id=thread_id
        )
        thread = await langgraph_client.threads.get(thread_id)
        context = SourceContext.from_metadata(thread_metadata(thread)).dump()
        context.pop("slack_thread", None)
        await langgraph_client.threads.update(
            thread_id=thread_id,
            metadata={
                "source": DASHBOARD_SOURCE,
                "source_context": context,
                SLACK_DETACHED_AT_KEY: now_iso(),
                SLACK_DETACHED_FROM_KEY: {"channel_id": channel_id, "thread_ts": thread_ts},
            },
        )
    except Exception:
        logger.exception(
            "Could not move thread off its deleted Slack thread",
            extra={"agent_thread_id": thread_id, "slack_channel": channel_id},
        )
        return False
    logger.warning(
        "Moved thread to the dashboard after its Slack thread was deleted",
        extra={
            "agent_thread_id": thread_id,
            "slack_channel": channel_id,
            "slack_thread_ts": thread_ts,
        },
    )
    return True


def dashboard_handoff_message(thread_id: str) -> str:
    """What to tell the model once Slack is no longer a destination."""
    url = dashboard_thread_url(thread_id)
    location = f" It continues on the web at {url}." if url else ""
    return (
        "The Slack thread you were replying in no longer exists, so this thread has "
        f"moved to the dashboard.{location} Do not post to Slack again: give your answer "
        "as your final response instead."
    )
