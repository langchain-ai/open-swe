"""Slack interactivity for expedited approvals: the card's button clicks."""

from fastapi import BackgroundTasks

from agent.expedited_review import card
from agent.expedited_review.voting import CardAction, process_vote
from agent.input_messages import PersonIdentity
from agent.slack.payloads import SlackButtonValue, SlackInteraction
from agent.slack.responses import WebhookResponse, accepted, ignored

BUTTON_TYPE = card.BUTTON_TYPE


def slack_person(slack_user_id: str) -> PersonIdentity:
    return {"id": f"slack:{slack_user_id}"}


async def handle_button(
    interaction: SlackInteraction, button: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    channel_id = interaction.channel_id
    thread_ts = interaction.thread_ts
    user_id = interaction.user.id
    if not channel_id or not thread_ts or not button.fingerprint or not user_id:
        return ignored("Missing expedited review context")
    decision: CardAction
    if button.action == "approve":
        decision = "approve"
    elif button.action == "ready":
        decision = "ready"
    elif button.action == "broadcast":
        decision = "broadcast"
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
    )
    return accepted("Expedited review vote queued")
