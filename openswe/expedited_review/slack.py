"""Slack interactivity for expedited approvals: the card's clicks and its channel picker."""

import logging

from fastapi import BackgroundTasks
from pydantic import ValidationError

from openswe.expedited_review import card
from openswe.expedited_review.voting import CardAction, process_vote
from openswe.input_messages import PersonIdentity
from openswe.slack.blocks import conversation_input, modal, view_payload
from openswe.slack.client import open_slack_modal
from openswe.slack.payloads import SlackButtonValue, SlackInteraction, SlackModalOrigin
from openswe.slack.responses import FeedbackResponse, WebhookResponse, accepted, ignored

logger = logging.getLogger(__name__)

BUTTON_TYPE = card.BUTTON_TYPE
CHANNEL_SELECT_ACTION = card.CHANNEL_SELECT_ACTION
CHANNEL_MODAL = "expedited_review_channel"
_MODAL_BLOCK = "channel"
_MODAL_ACTION = "channel"


class _PickerOrigin(SlackModalOrigin):
    """The card and the click that opened the channel picker."""

    approval_id: str
    thread_ts: str


def slack_person(slack_user_id: str) -> PersonIdentity:
    return {"id": f"slack:{slack_user_id}"}


async def _open_picker(interaction: SlackInteraction, approval_id: str) -> WebhookResponse:
    origin = _PickerOrigin(
        channel_id=interaction.channel_id, approval_id=approval_id, thread_ts=interaction.thread_ts
    )
    view = modal(
        callback_id=CHANNEL_MODAL,
        title="Send expedited review",
        blocks=[
            conversation_input(
                block_id=_MODAL_BLOCK,
                label="Channel",
                action_id=_MODAL_ACTION,
                include=["public"],
            )
        ],
        submit="Send",
        close="Cancel",
        private_metadata=origin.model_dump_json(),
    )
    if not interaction.trigger_id or not await open_slack_modal(
        interaction.trigger_id, view_payload(view)
    ):
        return ignored("Could not open the channel picker")
    return accepted("Channel picker opened")


def _picked_channel(interaction: SlackInteraction) -> str:
    picked = interaction.state.input(card.SEND_BLOCK_ID, CHANNEL_SELECT_ACTION).selected_option
    return picked.value if picked is not None else ""


def _other_approval_id(picked: str) -> str | None:
    """The card an "Other…" pick belongs to; ``None`` for a channel."""
    prefix, _, approval_id = picked.partition(":")
    return approval_id if prefix == card.OTHER_CHANNEL and approval_id else None


async def handle_channel_select(interaction: SlackInteraction) -> WebhookResponse:
    """Picking "Other…" opens the picker at once; any other pick waits for Send."""
    approval_id = _other_approval_id(_picked_channel(interaction))
    if approval_id is None:
        return ignored("Channel picked")
    return await _open_picker(interaction, approval_id)


async def handle_button(
    interaction: SlackInteraction, button: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    channel_id = interaction.channel_id
    thread_ts = interaction.thread_ts
    user_id = interaction.user.id
    if not channel_id or not thread_ts or not button.fingerprint or not user_id:
        return ignored("Missing expedited review context")
    target_channel = ""
    decision: CardAction
    if button.action == "approve":
        decision = "approve"
    elif button.action == "ready":
        decision = "ready"
    elif button.action == "broadcast":
        decision = "broadcast"
    elif button.action == "other":
        return await _open_picker(interaction, button.fingerprint)
    elif button.action == "send":
        target_channel = _picked_channel(interaction)
        if _other_approval_id(target_channel) is not None:
            return await _open_picker(interaction, button.fingerprint)
        if not target_channel:
            return ignored("No channel picked")
        decision = "send"
    elif button.action in {"dismiss", "reject"}:
        # Cards already posted in Slack still carry a Reject button.
        decision = "dismiss"
    else:
        return ignored("Unknown expedited review action")

    background_tasks.add_task(
        process_vote,
        button.fingerprint,
        decision=decision,
        person=slack_person(user_id),
        channel_id=channel_id,
        thread_ts=thread_ts,
        target_channel=target_channel,
    )
    return accepted("Expedited review vote queued")


def handle_picker_submission(
    interaction: SlackInteraction, background_tasks: BackgroundTasks
) -> FeedbackResponse:
    """Send the card to the channel picked in the modal; the answer arrives ephemerally."""
    picked = interaction.view.state.input(_MODAL_BLOCK, _MODAL_ACTION).selected_conversation
    if not picked:
        return {"response_action": "errors", "errors": {_MODAL_BLOCK: "Pick a channel."}}
    try:
        origin = _PickerOrigin.model_validate_json(interaction.view.private_metadata)
    except ValidationError:
        logger.warning("Expedited review channel picker lost its origin", exc_info=True)
        return {"response_action": "errors", "errors": {_MODAL_BLOCK: "Reopen this from the card."}}
    if not interaction.user.id:
        return {"response_action": "errors", "errors": {_MODAL_BLOCK: "Reopen this from the card."}}
    background_tasks.add_task(
        process_vote,
        origin.approval_id,
        decision="send",
        person=slack_person(interaction.user.id),
        channel_id=origin.channel_id,
        thread_ts=origin.thread_ts,
        target_channel=picked,
    )
    return {}
