"""Slack interactivity for standard human review cards: I'll review and Dismiss."""

from fastapi import BackgroundTasks

from agent.human_review import card
from agent.human_review.clicks import answer_click
from agent.human_review.lifecycle import dismiss_request
from agent.human_review.people import Outcome
from agent.human_review.requests import HumanReviewRequest
from agent.human_review.standard import claim
from agent.slack.payloads import SlackButtonValue, SlackInteraction
from agent.slack.responses import WebhookResponse, accepted, ignored
from agent.users import User

BUTTON_TYPE = card.BUTTON_TYPE


async def _process(
    request_id: str, action: str, *, channel_id: str, thread_ts: str, slack_user_id: str
) -> None:
    async def handle(request: HumanReviewRequest) -> Outcome:
        if action == "dismiss":
            return await dismiss_request(request, slack_user_id)
        return await claim(request, await User.for_person({"id": f"slack:{slack_user_id}"}))

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
    if not channel_id or not thread_ts or not button.fingerprint or not user_id:
        return ignored("Missing human review context")
    if button.action not in {"review", "dismiss"}:
        return ignored("Unknown human review action")
    background_tasks.add_task(
        _process,
        button.fingerprint,
        button.action,
        channel_id=channel_id,
        thread_ts=thread_ts,
        slack_user_id=user_id,
    )
    return accepted("Human review click queued")
