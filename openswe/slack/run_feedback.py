"""Native Slack ratings on individual agent replies."""

import json
import logging
from typing import Literal

from openswe.analytics.feedback import record_feedback_submission
from openswe.slack.blocks import ContextActionsBlock, plain_text
from openswe.slack.channels import SlackChannel
from openswe.slack.client import slack_thread_mutation_lock
from openswe.slack.http import SLACK_REQUEST_ERRORS, SlackClient, slack_identity
from openswe.slack.payloads import (
    SlackBlockAction,
    SlackInteraction,
    SlackPayload,
    parse_json_object,
)
from openswe.slack.records import lookup_slack_run_mapping
from openswe.utils.json_types import JsonObject
from openswe.utils.langsmith import create_langsmith_feedback
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

FEEDBACK_ACTION = "open_swe_run_feedback"


class RunFeedbackValue(SlackPayload):
    run_id: str
    rating: Literal["up", "down"]


def feedback_block(run_id: str) -> ContextActionsBlock:
    return {
        "type": "context_actions",
        "elements": [
            {
                "type": "feedback_buttons",
                "action_id": FEEDBACK_ACTION,
                "positive_button": {
                    "text": plain_text("Helpful"),
                    "value": json.dumps({"run_id": run_id, "rating": "up"}),
                    "accessibility_label": "Rate this reply helpful",
                },
                "negative_button": {
                    "text": plain_text("Not helpful"),
                    "value": json.dumps({"run_id": run_id, "rating": "down"}),
                    "accessibility_label": "Rate this reply not helpful",
                },
            }
        ],
    }


def _without_feedback(blocks: list[JsonObject]) -> list[JsonObject]:
    remaining: list[JsonObject] = []
    for block in blocks:
        elements = block.get("elements")
        if block.get("type") != "context_actions" or not isinstance(elements, list):
            remaining.append(block)
            continue
        kept = [
            element
            for element in elements
            if not isinstance(element, dict) or element.get("action_id") != FEEDBACK_ACTION
        ]
        if kept == elements:
            remaining.append(block)
        elif kept:
            remaining.append({**block, "elements": kept})
    return remaining


async def remove_earlier_feedback(channel_id: str, thread_ts: str, message_ts: str) -> None:
    """Remove our rating controls from replies preceding the newly posted message."""
    try:
        async with SlackClient.bot() as slack:
            identity = await slack_identity(slack)
            bot_user_id = identity.get("user_id")
            if not bot_user_id:
                return
            cursor = None
            while True:
                page = await slack.conversations_replies(
                    channel=channel_id, ts=thread_ts, latest=message_ts, limit=200, cursor=cursor
                )
                for message in page.get("messages", []):
                    if message.get("user") != bot_user_id or message.get("ts", "") >= message_ts:
                        continue
                    blocks = message.get("blocks", [])
                    remaining = _without_feedback(blocks)
                    if remaining != blocks:
                        try:
                            await slack.chat_update(
                                channel=channel_id,
                                ts=message["ts"],
                                text=message.get("text", ""),
                                blocks=remaining,
                                unfurl_links=False,
                                unfurl_media=False,
                            )
                        except SLACK_REQUEST_ERRORS:
                            logger.warning(
                                "Could not remove earlier Slack reply feedback", exc_info=True
                            )
                cursor = page.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
    except SLACK_REQUEST_ERRORS:
        logger.warning("Could not fetch earlier Slack reply feedback", exc_info=True)


async def process_feedback(interaction: SlackInteraction, action: SlackBlockAction) -> None:
    try:
        selection = RunFeedbackValue.parse(parse_json_object((action.value or "{}").encode()))
        channel_id = interaction.channel_id
        message_ts = interaction.message_ts
        user_id = interaction.user.id
        if selection is None or not (channel_id and message_ts and user_id):
            return
        client = langgraph_client()
        mapping = await lookup_slack_run_mapping(channel_id, message_ts)
        if (
            mapping is None
            or mapping.run_id != selection.run_id
            or mapping.triggering_user_id != user_id
        ):
            return
        context = await SlackChannel.context_for(channel_id, use_cache=False)
        if not context.allows_operations:
            return
        key = f"slack_reply:{channel_id}:{user_id}:{message_ts}"
        async with slack_thread_mutation_lock(
            client, channel_id, message_ts, purpose=f"run_feedback:{user_id}"
        ):
            saved = await create_langsmith_feedback(
                selection.run_id,
                key,
                score=1.0 if selection.rating == "up" else 0.0,
                source_info={
                    "source": "slack_reply",
                    "channel_id": channel_id,
                    "message_ts": message_ts,
                    "user_id": user_id,
                },
            )
        if saved:
            await record_feedback_submission(
                feedback_key=f"run:{selection.run_id}:{key}",
                rating=5 if selection.rating == "up" else 1,
                source="slack",
                run_key=selection.run_id,
                slack_user_id=user_id,
            )
        else:
            logger.warning(
                "Could not save Slack reply feedback", extra={"feedback_run_id": selection.run_id}
            )
    except Exception:
        logger.exception("Could not process Slack reply feedback")
