"""Slack interactivity for standard human review cards: I'll review, Accept, and Dismiss."""

import logging

from fastapi import BackgroundTasks

from openswe.human_review import card
from openswe.human_review.clicks import answer_click
from openswe.human_review.lifecycle import dismiss_request
from openswe.human_review.people import Outcome
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import claim
from openswe.prompts import prompt
from openswe.slack.dm import note_for_concierge
from openswe.slack.payloads import SlackButtonValue, SlackInteraction
from openswe.slack.responses import WebhookResponse, accepted, ignored
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.users import User

logger = logging.getLogger(__name__)

BUTTON_TYPE = card.BUTTON_TYPE


async def _process(
    request_id: str, action: str, *, channel_id: str, thread_ts: str, slack_user_id: str
) -> None:
    async def handle(request: HumanReviewRequest) -> Outcome:
        if action == "dismiss":
            outcome = await dismiss_request(request, slack_user_id)
        else:
            user = await User.for_person({"id": f"slack:{slack_user_id}"})
            outcome = await claim(request, user)
        note = prompt(
            "slack/review-request-clicked",
            action=action,
            pr_url=request.pull_request.url,
            outcome=outcome.message,
        )
        origin = request.dm_origin
        await note_for_thread_owner(*(origin.location if origin else (channel_id, thread_ts)), note)
        if channel_id.startswith("D"):
            await note_for_concierge(slack_user_id, channel_id, note)
        return outcome

    await answer_click(
        request_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        slack_user_id=slack_user_id,
        handle=handle,
    )


async def handle_button(
    interaction: SlackInteraction, button: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    channel_id = interaction.channel_id
    thread_ts = interaction.thread_ts
    user_id = interaction.user.id
    extra = {
        "request_id": button.fingerprint,
        "button_action": button.action,
        "slack_user": user_id,
        "slack_channel": channel_id,
        "slack_thread_ts": thread_ts,
    }
    if not channel_id or not thread_ts or not button.fingerprint or not user_id:
        logger.warning("Ignored a human review click missing its context", extra=extra)
        return ignored("Missing human review context")
    if button.action not in {"review", "dismiss"}:
        logger.warning("Ignored an unknown human review click", extra=extra)
        return ignored("Unknown human review action")
    logger.info("Queued a human review click", extra=extra)
    background_tasks.add_task(
        _process,
        button.fingerprint,
        button.action,
        channel_id=channel_id,
        thread_ts=thread_ts,
        slack_user_id=user_id,
    )
    return accepted("Human review click queued")
