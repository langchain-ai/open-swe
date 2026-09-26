"""Native Slack ratings on individual agent replies."""

import asyncio
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
from agent.slack.payloads import (
    SlackBlockAction,
    SlackInteraction,
    SlackPayload,
    parse_json_object,
)
from agent.slack.responses import FeedbackResponse
from agent.utils.json_types import JsonObject
from agent.utils.langsmith import create_langsmith_feedback
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

FEEDBACK_ACTION = "open_swe_run_feedback"
FEEDBACK_NOTE_ACTION = "open_swe_run_feedback_note"
COMMENT_BLOCK = "run_feedback_comment"
COMMENT_MAX_LENGTH = 3000


class RunFeedbackValue(SlackPayload):
    run_id: str
    rating: Literal["up", "down"]


class RunFeedbackMetadata(SlackPayload):
    """What a comment modal carries so its submission can find the rated reply."""

    run_id: str
    channel_id: str
    message_ts: str
    user_id: str


def _feedback_key(channel_id: str, user_id: str, message_ts: str) -> str:
    return f"slack_reply:{channel_id}:{user_id}:{message_ts}"


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


def comment_modal(metadata: RunFeedbackMetadata) -> dict[str, object]:
    return {
        "type": "modal",
        "callback_id": FEEDBACK_NOTE_ACTION,
        "private_metadata": metadata.model_dump_json(),
        "title": {"type": "plain_text", "text": "What went wrong?"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Skip"},
        "blocks": [
            {
                "type": "input",
                "block_id": COMMENT_BLOCK,
                "optional": True,
                "label": {"type": "plain_text", "text": "What could the agent do better?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "comment",
                    "multiline": True,
                    "max_length": COMMENT_MAX_LENGTH,
                },
            }
        ],
    }


def is_run_feedback_submission(payload: JsonObject) -> bool:
    """True for a modal submission from the per-reply comment form."""
    view = payload.get("view")
    return isinstance(view, dict) and view.get("callback_id") == FEEDBACK_NOTE_ACTION


def _metadata(payload: JsonObject) -> RunFeedbackMetadata | None:
    view = payload.get("view")
    raw = view.get("private_metadata") if isinstance(view, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return RunFeedbackMetadata.parse(parsed) if isinstance(parsed, dict) else None


def _submitted_comment(payload: JsonObject) -> str:
    view = payload.get("view")
    state = view.get("state") if isinstance(view, dict) else None
    values = state.get("values") if isinstance(state, dict) else None
    block = values.get(COMMENT_BLOCK) if isinstance(values, dict) else None
    element = block.get("comment") if isinstance(block, dict) else None
    raw = element.get("value") if isinstance(element, dict) else None
    return raw.strip() if isinstance(raw, str) else ""


async def _save_comment(metadata: RunFeedbackMetadata, comment: str) -> bool:
    client = langgraph_client()
    mapping = await lookup_slack_run_mapping(client, metadata.channel_id, metadata.message_ts)
    if not mapping or mapping.get("run_id") != metadata.run_id:
        return False
    async with slack_thread_mutation_lock(
        client,
        metadata.channel_id,
        metadata.message_ts,
        purpose=f"run_feedback_comment:{metadata.user_id}",
    ):
        return bool(
            await create_langsmith_feedback(
                metadata.run_id,
                _feedback_key(metadata.channel_id, metadata.user_id, metadata.message_ts),
                score=0.0,
                comment=comment,
                source_info={
                    "source": "slack_reply",
                    "channel_id": metadata.channel_id,
                    "message_ts": metadata.message_ts,
                    "user_id": metadata.user_id,
                },
            )
        )


def _comment_error(text: str) -> FeedbackResponse:
    return {"response_action": "errors", "errors": {COMMENT_BLOCK: text}}


async def handle_run_feedback_submission(payload: JsonObject) -> FeedbackResponse:
    """Persist the text a user typed into the thumbs-down comment modal."""
    try:
        async with asyncio.timeout(2.5):
            metadata = _metadata(payload)
            if metadata is None:
                return _comment_error("This feedback is unavailable. Please try rating again.")
            comment = _submitted_comment(payload)
            # An empty submit just re-confirms the rating the button already recorded;
            # saving would overwrite any comment stored earlier.
            if not comment:
                return {}
            if not await _save_comment(metadata, comment):
                return _comment_error("Your feedback could not be saved. Please try again.")
            return {}
    except Exception:
        logger.exception("Could not save Slack reply feedback comment")
        return _comment_error("Your feedback could not be saved. Please try again.")


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
        if selection.rating == "down" and interaction.trigger_id:
            await open_slack_modal(
                interaction.trigger_id,
                comment_modal(
                    RunFeedbackMetadata(
                        run_id=selection.run_id,
                        channel_id=channel_id,
                        message_ts=message_ts,
                        user_id=user_id,
                    )
                ),
            )
        async with slack_thread_mutation_lock(
            client, channel_id, message_ts, purpose=f"run_feedback:{user_id}"
        ):
            saved = await create_langsmith_feedback(
                selection.run_id,
                _feedback_key(channel_id, user_id, message_ts),
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
                feedback_key=f"run:{selection.run_id}:{_feedback_key(channel_id, user_id, message_ts)}",
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
