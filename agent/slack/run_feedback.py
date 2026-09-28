"""Native Slack ratings on individual agent replies."""

import json
import logging
from typing import Literal

from agent.analytics.feedback import record_feedback_submission
from agent.slack.blocks import ContextActionsBlock, plain_text
from agent.slack.channels import SlackChannel
from agent.slack.client import (
    lookup_slack_run_mapping,
    open_slack_modal,
    slack_thread_mutation_lock,
)
from agent.slack.payloads import SlackBlockAction, SlackInteraction, SlackPayload, parse_json_object
from agent.slack.responses import FeedbackResponse
from agent.utils.langsmith import create_langsmith_feedback
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

FEEDBACK_ACTION = "open_swe_run_feedback"

NOTE_ACTION = "open_swe_run_feedback_note"
NOTE_BLOCK = "run_feedback_note"


def is_run_feedback_note(payload: dict[str, object]) -> bool:
    view = payload.get("view")
    return (
        payload.get("type") == "view_submission"
        and isinstance(view, dict)
        and view.get("callback_id") == NOTE_ACTION
    )


def note_modal(run_id: str, channel_id: str, message_ts: str) -> dict[str, object]:
    return {
        "type": "modal",
        "callback_id": NOTE_ACTION,
        "private_metadata": json.dumps(
            {"run_id": run_id, "channel_id": channel_id, "message_ts": message_ts}
        ),
        "title": {"type": "plain_text", "text": "Open SWE feedback"},
        "submit": {"type": "plain_text", "text": "Send feedback"},
        "close": {"type": "plain_text", "text": "Skip"},
        "blocks": [
            {
                "type": "input",
                "block_id": NOTE_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "How could Open SWE do better?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "comment",
                    "multiline": True,
                    "max_length": 3000,
                    "placeholder": {"type": "plain_text", "text": "What could be better?"},
                },
            }
        ],
    }


async def open_run_feedback_note(interaction: SlackInteraction, action: SlackBlockAction) -> None:
    selection = RunFeedbackValue.parse(parse_json_object((action.value or "{}").encode()))
    if selection is None or selection.rating != "down" or not interaction.trigger_id:
        return
    channel_id, message_ts, user_id = (
        interaction.channel_id,
        interaction.message_ts,
        interaction.user.id,
    )
    if not (channel_id and message_ts and user_id):
        return
    mapping = await lookup_slack_run_mapping(langgraph_client(), channel_id, message_ts)
    if (
        not mapping
        or mapping.get("run_id") != selection.run_id
        or mapping.get("triggering_user_id") != user_id
    ):
        return
    if not (await SlackChannel.context_for(channel_id, use_cache=False)).allows_operations:
        return
    await open_slack_modal(
        interaction.trigger_id, note_modal(selection.run_id, channel_id, message_ts)
    )


async def submit_run_feedback_note(payload: dict[str, object]) -> FeedbackResponse:
    view = payload.get("view")
    user = payload.get("user")
    if not isinstance(view, dict) or not isinstance(user, dict):
        return {}
    try:
        metadata = json.loads(str(view.get("private_metadata") or "{}"))
        run_id, channel_id, message_ts, user_id = (
            metadata.get("run_id"),
            metadata.get("channel_id"),
            metadata.get("message_ts"),
            user.get("id"),
        )
        if not all(
            isinstance(value, str) and value for value in (run_id, channel_id, message_ts, user_id)
        ):
            return {}
        state = view.get("state")
        values = state.get("values") if isinstance(state, dict) else None
        block = values.get(NOTE_BLOCK) if isinstance(values, dict) else None
        input_value = block.get("comment") if isinstance(block, dict) else None
        raw = input_value.get("value") if isinstance(input_value, dict) else None
        if raw is not None and (not isinstance(raw, str) or len(raw) > 3000):
            return {
                "response_action": "errors",
                "errors": {NOTE_BLOCK: "Comments must be at most 3,000 characters."},
            }
        comment = raw.strip() if isinstance(raw, str) else ""
        if not comment:
            return {}
        client = langgraph_client()
        mapping = await lookup_slack_run_mapping(client, channel_id, message_ts)
        if (
            not mapping
            or mapping.get("run_id") != run_id
            or mapping.get("triggering_user_id") != user_id
        ):
            return {}
        if not (await SlackChannel.context_for(channel_id, use_cache=False)).allows_operations:
            return {}
        key = f"slack_reply:{channel_id}:{user_id}:{message_ts}"
        async with slack_thread_mutation_lock(
            client, channel_id, message_ts, purpose=f"run_feedback:{user_id}"
        ):
            saved = await create_langsmith_feedback(
                run_id,
                key,
                score=0.0,
                comment=comment,
                source_info={
                    "source": "slack_reply",
                    "channel_id": channel_id,
                    "message_ts": message_ts,
                    "user_id": user_id,
                },
            )
        if not saved:
            return {
                "response_action": "errors",
                "errors": {NOTE_BLOCK: "Could not save feedback. Please try again."},
            }
    except Exception:
        logger.exception("Could not save Slack reply feedback note")
        return {
            "response_action": "errors",
            "errors": {NOTE_BLOCK: "Could not save feedback. Please try again."},
        }
    return {}


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
