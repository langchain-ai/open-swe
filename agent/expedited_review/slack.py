"""Slack interactivity for expedited approvals: button clicks and the reject modal."""

import logging
from collections.abc import Mapping
from uuid import UUID

from fastapi import BackgroundTasks

from agent.expedited_review import card
from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.voting import process_vote
from agent.input_messages import PersonIdentity
from agent.slack.blocks import view_payload
from agent.slack.client import open_slack_modal, post_slack_ephemeral_message
from agent.slack.payloads import SlackButtonValue, SlackInteraction, SlackViewSubmission
from agent.slack.responses import FeedbackResponse, WebhookResponse, accepted, ignored

logger = logging.getLogger(__name__)


def slack_person(slack_user_id: str, handle: str = "") -> PersonIdentity:
    person: PersonIdentity = {"id": f"slack:{slack_user_id}", "platform": "slack"}
    if handle:
        person["handle"] = handle
    return person


def is_expedited_review_submission(payload: Mapping[str, object]) -> bool:
    view = payload.get("view")
    return payload.get("type") == "view_submission" and (
        isinstance(view, Mapping) and view.get("callback_id") == card.REJECT_MODAL_CALLBACK
    )


async def handle_button(
    interaction: SlackInteraction, button: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    channel_id = interaction.channel_id
    thread_ts = interaction.thread_ts
    user_id = interaction.user.id
    if not channel_id or not thread_ts or not button.fingerprint or not user_id:
        return ignored("Missing expedited review context")
    if button.action not in {"approve", "reject"}:
        return ignored("Unknown expedited review action")

    if button.action == "reject" and interaction.trigger_id:
        try:
            approval = await ExpeditedApproval.get(UUID(button.fingerprint))
        except ValueError:
            approval = None
        if approval is None or approval.state != "open":
            background_tasks.add_task(
                post_slack_ephemeral_message,
                channel_id,
                user_id,
                "This expedited review is no longer accepting votes.",
                thread_ts,
            )
            return accepted("Expedited review closed")
        opened = await open_slack_modal(
            interaction.trigger_id,
            view_payload(card.reject_modal(approval, channel_id=channel_id, thread_ts=thread_ts)),
        )
        if opened:
            return accepted("Reject feedback requested")

    background_tasks.add_task(
        process_vote,
        button.fingerprint,
        decision="approve" if button.action == "approve" else "reject",
        person=slack_person(user_id, interaction.user.username or interaction.user.name),
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    return accepted("Expedited review vote queued")


async def handle_submission(
    payload: Mapping[str, object], background_tasks: BackgroundTasks
) -> FeedbackResponse:
    submission = SlackViewSubmission.parse(payload)
    metadata = submission.metadata if submission is not None else {}
    approval_id = str(metadata.get("approval_id") or "")
    channel_id = str(metadata.get("channel_id") or "")
    thread_ts = str(metadata.get("thread_ts") or "")
    user_id = submission.user.id if submission is not None else ""
    if submission is None or not (approval_id and channel_id and thread_ts and user_id):
        return {
            "response_action": "errors",
            "errors": {
                card.REJECT_FEEDBACK_BLOCK: "This form has expired. Reopen it from the card."
            },
        }
    background_tasks.add_task(
        process_vote,
        approval_id,
        decision="reject",
        person=slack_person(user_id, submission.user.username or submission.user.name),
        channel_id=channel_id,
        thread_ts=thread_ts,
        feedback=submission.submitted(card.REJECT_FEEDBACK_BLOCK, card.REJECT_FEEDBACK_ACTION),
    )
    return {}
