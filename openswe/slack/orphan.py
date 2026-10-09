"""Recovery for an agent thread whose Slack thread no longer exists.

Slack does not reject a reply to a deleted parent: it posts it at the channel
root, where it reads as an answer to nothing. Such a thread is moved to the
web so the rest of the conversation has somewhere to go.
"""

import logging

from langgraph_sdk.client import LangGraphClient

from openswe.slack.client import (
    SLACK_DETACHED_AT_KEY,
    SLACK_DETACHED_FROM_KEY,
    delete_slack_thread_associations,
)
from openswe.source_context import SourceContext
from openswe.store import now_iso
from openswe.threads.summary import WEB_APP_SOURCE
from openswe.utils.json_types import JsonObject, thread_metadata
from openswe.utils.web_links import web_thread_url

logger = logging.getLogger(__name__)


def slack_thread_detached(metadata: JsonObject) -> bool:
    """Whether this thread has already been moved off Slack."""
    return bool(metadata.get(SLACK_DETACHED_AT_KEY)) and (
        SourceContext.from_metadata(metadata).slack_thread is None
    )


async def move_thread_to_web(
    langgraph_client: LangGraphClient,
    thread_id: str,
    channel_id: str,
    thread_ts: str,
) -> bool:
    """Detach an agent thread from its Slack thread and keep it in the web app."""
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
                "source": WEB_APP_SOURCE,
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
        "Moved thread to the web app after its Slack thread was deleted",
        extra={
            "agent_thread_id": thread_id,
            "slack_channel": channel_id,
            "slack_thread_ts": thread_ts,
        },
    )
    return True


def web_handoff_message(thread_id: str) -> str:
    """What to tell the model once Slack is no longer a destination."""
    url = web_thread_url(thread_id)
    location = f" It continues on the web at {url}." if url else ""
    return (
        "The Slack thread you were replying in no longer exists, so this thread has "
        f"moved to the web app.{location} Do not post to Slack again: give your answer "
        "as your final response instead."
    )
