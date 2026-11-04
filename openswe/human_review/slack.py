"""Slack interactivity for human review cards and picks: I'll review, Accept, Decline, Snooze, and Dismiss."""

import logging
from uuid import UUID

from fastapi import BackgroundTasks
from pydantic import BaseModel, ValidationError

from openswe.human_review import card, readiness
from openswe.human_review.clicks import answer_click
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.people import Outcome
from openswe.human_review.pick_message import PickMessage
from openswe.human_review.pick_modals import CHOICE, MODAL_FOR_BUTTON, PICK_MODALS, PickModal
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import claim, decline, snooze
from openswe.prompts import prompt
from openswe.slack.blocks import view_payload
from openswe.slack.client import open_slack_modal
from openswe.slack.dm import note_for_concierge
from openswe.slack.payloads import SlackButtonValue, SlackInteraction
from openswe.slack.responses import FeedbackResponse, WebhookResponse, accepted, ignored
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.users import User

logger = logging.getLogger(__name__)

BUTTON_TYPE = card.BUTTON_TYPE
_CHOICE = CHOICE

_PENDING = {
    "review": ":hourglass_flowing_sand: Accepting…",
    "decline": ":hourglass_flowing_sand: Declining…",
    "snooze": ":hourglass_flowing_sand: Snoozing…",
}


class PickModalContext(BaseModel):
    """``private_metadata`` of a pick modal the backend opened itself, before Trigger Server did."""

    request_id: str
    channel_id: str
    thread_ts: str
    user_id: str
    message: PickMessage | None = None


async def handle_pick_modal_submission(
    interaction: SlackInteraction, background_tasks: BackgroundTasks
) -> FeedbackResponse | WebhookResponse:
    pick_modal = PICK_MODALS[interaction.view.callback_id]
    try:
        context = PickModalContext.model_validate_json(interaction.view.private_metadata)
    except ValidationError:
        logger.warning("Invalid reviewer pick modal context", exc_info=True)
        return ignored("Invalid pick modal context")
    selected = interaction.view.state.input(_CHOICE, _CHOICE).selected_option
    if (
        context.user_id != interaction.user.id
        or selected is None
        or selected.value not in pick_modal.choices
    ):
        return ignored("Invalid pick modal submission")
    background_tasks.add_task(
        _process,
        context.request_id,
        pick_modal.action,
        channel_id=context.channel_id,
        thread_ts=context.thread_ts,
        slack_user_id=interaction.user.id,
        choice=selected.value,
        message=context.message,
    )
    return {}


def handle_pick_submission(
    interaction: SlackInteraction,
    pick_modal: PickModal,
    request_id: str,
    origin: tuple[str, str],
    background_tasks: BackgroundTasks,
) -> WebhookResponse:
    """A pick modal Trigger Server opened: ``origin`` is the pick's DM message."""
    selected = interaction.view.state.input(_CHOICE, _CHOICE).selected_option
    if not interaction.user.id or selected is None or selected.value not in pick_modal.choices:
        return ignored("Invalid pick modal submission")
    channel_id, message_ts = origin
    background_tasks.add_task(
        _process_on_message,
        request_id,
        pick_modal.action,
        channel_id=channel_id,
        message_ts=message_ts,
        slack_user_id=interaction.user.id,
        choice=selected.value,
    )
    return accepted("Pick modal submission queued")


async def _process_on_message(
    request_id: str,
    action: str,
    *,
    channel_id: str,
    message_ts: str,
    slack_user_id: str,
    choice: str,
) -> None:
    """Act on a pick from its DM message, edited in place when Slack still has it."""
    await _process(
        request_id,
        action,
        channel_id=channel_id,
        thread_ts=message_ts,
        slack_user_id=slack_user_id,
        choice=choice,
        message=await PickMessage.fetch(channel_id, message_ts),
    )


async def _process(
    request_id: str,
    action: str,
    *,
    channel_id: str,
    thread_ts: str,
    slack_user_id: str,
    choice: str = "",
    message: PickMessage | None = None,
    response_url: str = "",
) -> None:
    if message is not None:
        await message.show(_PENDING[action])
    handled: list[Outcome] = []

    async def handle(request: HumanReviewRequest) -> Outcome:
        if action == "dismiss":
            outcome = await ReviewCard(request).dismiss(slack_user_id)
        else:
            user = await User.for_person({"id": f"slack:{slack_user_id}"})
            if action == "decline":
                outcome = await decline(request, user, choice)
            elif action == "snooze":
                outcome = await snooze(request, user, choice)
            else:
                outcome = await claim(request, user)
        note = prompt(
            "slack/review-request-clicked",
            action=action,
            choice=choice,
            pr_url=request.pull_request.url,
            outcome=outcome.message,
        )
        origin = request.dm_origin
        await note_for_thread_owner(*(origin.location if origin else (channel_id, thread_ts)), note)
        if channel_id.startswith("D"):
            await note_for_concierge(slack_user_id, channel_id, note)
        handled.append(outcome)
        return outcome

    outcome = await answer_click(
        request_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        slack_user_id=slack_user_id,
        handle=handle,
        ephemeral=message is None,
        response_url=response_url,
    )
    if message is None:
        return
    if handled:
        await message.show(outcome.message)
        return
    # The click never ran, so its buttons come back for another try.
    request = await HumanReviewRequest.get(UUID(request_id))
    buttons = (
        (card.accept_button(request), card.decline_button(request), card.snooze_button(request))
        if request is not None and request.state == "open"
        else ()
    )
    await message.show(outcome.message, buttons)


async def handle_button(
    interaction: SlackInteraction, button: SlackButtonValue, background_tasks: BackgroundTasks
) -> WebhookResponse:
    if button.type == readiness.BUTTON_TYPE:
        return await readiness.handle_button(interaction, button, background_tasks)
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
    message = (
        PickMessage(
            channel_id=channel_id,
            ts=interaction.message.ts,
            text=interaction.message.text,
            response_url=interaction.response_url,
        )
        if interaction.message.ts
        and any(action.action_id in card.PICK_BUTTON_IDS for action in interaction.actions)
        else None
    )
    if (pick_modal := MODAL_FOR_BUTTON.get(button.action)) is not None:
        # A pick posted before its buttons opened their modals through Trigger Server.
        view = pick_modal.view(
            PickModalContext(
                request_id=button.fingerprint,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                message=message,
            ).model_dump_json()
        )
        if interaction.trigger_id and await open_slack_modal(
            interaction.trigger_id, view_payload(view)
        ):
            return accepted("Pick modal opened")
        logger.warning("Could not open a reviewer pick modal", extra=extra)
        if message is not None:
            # Too late to open it: the pick's current buttons open it through Trigger Server.
            request = await HumanReviewRequest.get(UUID(button.fingerprint))
            if request is not None and request.state == "open":
                await message.show(
                    "Click again to answer.",
                    (
                        card.accept_button(request),
                        card.decline_button(request),
                        card.snooze_button(request),
                    ),
                )
        return ignored("Could not open pick modal")
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
        message=message,
        response_url=interaction.response_url,
    )
    return accepted("Human review click queued")
