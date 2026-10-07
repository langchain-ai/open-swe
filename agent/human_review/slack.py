"""Slack interactivity for standard human review cards: I'll review, Accept, and Dismiss."""

import logging

from fastapi import BackgroundTasks
from pydantic import BaseModel, ValidationError

from agent.human_review import card
from agent.human_review.clicks import answer_click
from agent.human_review.lifecycle import dismiss_request
from agent.human_review.people import Outcome
from agent.human_review.requests import HumanReviewRequest
from agent.human_review.standard import claim, decline, snooze
from agent.prompts import prompt
from agent.slack.blocks import InputBlock, modal, option, plain_text, static_select, view_payload
from agent.slack.client import open_slack_modal
from agent.slack.dm import note_for_concierge
from agent.slack.payloads import SlackButtonValue, SlackInteraction
from agent.slack.responses import FeedbackResponse, WebhookResponse, accepted, ignored
from agent.users import User

logger = logging.getLogger(__name__)

BUTTON_TYPE = card.BUTTON_TYPE
DECLINE_MODAL = "human_review_decline"
DECLINE_REASONS = (
    "Too many reviews / not enough time",
    "Away or unavailable",
    "Not familiar with this code",
    "Someone else is a better reviewer",
    "Conflict of interest",
    "Other",
)


class DeclineContext(BaseModel):
    request_id: str
    channel_id: str
    thread_ts: str
    user_id: str


async def handle_decline_submission(
    interaction: SlackInteraction, background_tasks: BackgroundTasks
) -> FeedbackResponse | WebhookResponse:
    try:
        context = DeclineContext.model_validate_json(interaction.view.private_metadata)
    except ValidationError:
        logger.warning("Invalid reviewer decline modal context", exc_info=True)
        return ignored("Invalid decline context")
    selected = interaction.view.state.input("reason", "reason").selected_option
    if (
        context.user_id != interaction.user.id
        or selected is None
        or selected.value not in DECLINE_REASONS
    ):
        return ignored("Invalid decline submission")
    background_tasks.add_task(
        _process,
        context.request_id,
        "decline",
        channel_id=context.channel_id,
        thread_ts=context.thread_ts,
        slack_user_id=interaction.user.id,
        reason=selected.value,
    )
    return {}


async def _process(
    request_id: str,
    action: str,
    *,
    channel_id: str,
    thread_ts: str,
    slack_user_id: str,
    reason: str = "",
) -> None:
    async def handle(request: HumanReviewRequest) -> Outcome:
        if action == "dismiss":
            return await dismiss_request(request, slack_user_id)
        user = await User.for_person({"id": f"slack:{slack_user_id}"})
        if action == "decline":
            outcome = await decline(request, user, reason)
        elif action == "snooze":
            outcome = await snooze(request, user)
        else:
            outcome = await claim(request, user)
        if channel_id.startswith("D"):
            await note_for_concierge(
                slack_user_id,
                channel_id,
                prompt(
                    "slack/concierge-review-pick-clicked",
                    pr_url=request.pull_request.url,
                    outcome=outcome.message,
                ),
            )
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
    if button.action == "decline":
        reason_input: InputBlock = {
            "type": "input",
            "block_id": "reason",
            "label": plain_text("Why are you declining?"),
            "element": static_select(
                action_id="reason",
                options=[option(reason, reason) for reason in DECLINE_REASONS],
                placeholder="Select a reason",
            ),
        }
        view = modal(
            callback_id=DECLINE_MODAL,
            title="Decline review",
            blocks=[reason_input],
            submit="Decline",
            close="Cancel",
            private_metadata=DeclineContext(
                request_id=button.fingerprint,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
            ).model_dump_json(),
        )
        if not interaction.trigger_id or not await open_slack_modal(
            interaction.trigger_id, view_payload(view)
        ):
            return ignored("Could not open decline modal")
        return accepted("Decline modal opened")
    if button.action not in {"review", "dismiss", "snooze"}:
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
