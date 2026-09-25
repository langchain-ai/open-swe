"""Native Slack ratings on individual agent replies."""

import json
import logging
from typing import Literal

from agent.analytics.feedback import record_feedback_submission
from agent.slack.blocks import ContextActionsBlock, plain_text
from agent.slack.channels import SlackChannel
from agent.slack.client import lookup_slack_run_mapping, slack_thread_mutation_lock
from agent.slack.payloads import SlackBlockAction, SlackInteraction, SlackPayload, parse_json_object
from agent.utils.langsmith import create_langsmith_feedback
from agent.utils.thread_ops import langgraph_client

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


async def process_feedback(interaction: SlackInteraction, action: SlackBlockAction) -> None:
    try:
        selection = RunFeedbackValue.parse(parse_json_object((action.value or "{}").encode()))
        channel_id = interaction.channel_id
        message_ts = interaction.message_ts
        user_id = interaction.user.id
        if selection is None or not (channel_id and message_ts and user_id):
            return
        client = langgraph_client()
        mapping = await lookup_slack_run_mapping(client, channel_id, message_ts)
        if (
            not mapping
            or mapping.get("run_id") != selection.run_id
            or mapping.get("triggering_user_id") != user_id
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
