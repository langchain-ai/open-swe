"""Slack interactivity for human review cards and picks: I'll review, Accept, Decline, Snooze, and Dismiss."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import BackgroundTasks
from pydantic import BaseModel, ValidationError

from agent.human_review import card
from agent.human_review.clicks import answer_click
from agent.human_review.lifecycle import dismiss_request
from agent.human_review.people import Outcome
from agent.human_review.requests import HumanReviewRequest
from agent.human_review.standard import SNOOZE_DURATIONS, claim, decline, snooze
from agent.prompts import prompt
from agent.slack.blocks import (
    Block,
    ButtonElement,
    InputBlock,
    ModalView,
    actions,
    block_payload,
    context,
    modal,
    option,
    plain_text,
    section,
    static_select,
    view_payload,
)
from agent.slack.client import open_slack_modal, update_slack_message
from agent.slack.dm import note_for_concierge
from agent.slack.http import SlackRequestError
from agent.slack.payloads import SlackButtonValue, SlackInteraction
from agent.slack.responses import FeedbackResponse, WebhookResponse, accepted, ignored
from agent.slack.thread_owner import note_for_thread_owner
from agent.users import User

logger = logging.getLogger(__name__)

BUTTON_TYPE = card.BUTTON_TYPE
DECLINE_REASONS = (
    "Too many reviews / not enough time",
    "Away or unavailable",
    "Not familiar with this code",
    "Someone else is a better reviewer",
    "Conflict of interest",
    "Other",
)
_CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class PickModal:
    """A pick's button that asks one required question in a modal before acting."""

    action: Literal["decline", "snooze"]
    callback_id: str
    title: str
    question: str
    placeholder: str
    submit: str
    choices: tuple[str, ...]
    initial: str | None = None

    def view(self, context: PickModalContext) -> ModalView:
        select = static_select(
            action_id=_CHOICE,
            options=[option(choice, choice) for choice in self.choices],
            initial=option(self.initial, self.initial) if self.initial else None,
            placeholder=self.placeholder,
        )
        question: InputBlock = {
            "type": "input",
            "block_id": _CHOICE,
            "label": plain_text(self.question),
            "element": select,
        }
        return modal(
            callback_id=self.callback_id,
            title=self.title,
            blocks=[question],
            submit=self.submit,
            close="Cancel",
            private_metadata=context.model_dump_json(),
        )


class PickMessage(BaseModel):
    """The message a pick's buttons sit on, which shows a click's progress before the slow work."""

    channel_id: str
    ts: str
    text: str

    async def show(self, status: str, buttons: Sequence[ButtonElement] = ()) -> None:
        blocks: list[Block] = [section(self.text), context(status)]
        if buttons:
            blocks.append(actions(*buttons))
        try:
            await update_slack_message(
                self.channel_id, self.ts, self.text, blocks=block_payload(blocks)
            )
        except SlackRequestError as exc:
            logger.warning(
                "Could not update a reviewer pick message",
                extra={"slack_channel": self.channel_id, "slack_error": exc.code},
            )


_PENDING = {
    "review": ":hourglass_flowing_sand: Accepting…",
    "decline": ":hourglass_flowing_sand: Declining…",
    "snooze": ":hourglass_flowing_sand: Snoozing…",
}


class PickModalContext(BaseModel):
    request_id: str
    channel_id: str
    thread_ts: str
    user_id: str
    message: PickMessage | None = None


PICK_MODALS = {
    pick_modal.callback_id: pick_modal
    for pick_modal in (
        PickModal(
            action="decline",
            callback_id="human_review_decline",
            title="Decline review",
            question="Why are you declining?",
            placeholder="Select a reason",
            submit="Decline",
            choices=DECLINE_REASONS,
        ),
        PickModal(
            action="snooze",
            callback_id="human_review_snooze",
            title="Snooze review",
            question="Remind me in",
            placeholder="Select how long",
            submit="Snooze",
            choices=tuple(SNOOZE_DURATIONS),
            initial="1 hour",
        ),
    )
}
_MODAL_FOR_BUTTON = {pick_modal.action: pick_modal for pick_modal in PICK_MODALS.values()}


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


async def _process(
    request_id: str,
    action: str,
    *,
    channel_id: str,
    thread_ts: str,
    slack_user_id: str,
    choice: str = "",
    message: PickMessage | None = None,
) -> None:
    if message is not None:
        await message.show(_PENDING[action])
    handled: list[Outcome] = []

    async def handle(request: HumanReviewRequest) -> Outcome:
        if action == "dismiss":
            outcome = await dismiss_request(request, slack_user_id)
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
        if channel_id.startswith("D"):
            await note_for_concierge(slack_user_id, channel_id, note)
        else:
            await note_for_thread_owner(channel_id, thread_ts, note)
        handled.append(outcome)
        return outcome

    outcome = await answer_click(
        request_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        slack_user_id=slack_user_id,
        handle=handle,
        ephemeral=message is None,
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
        PickMessage(channel_id=channel_id, ts=interaction.message.ts, text=interaction.message.text)
        if interaction.message.ts
        and any(action.action_id in card.PICK_BUTTON_IDS for action in interaction.actions)
        else None
    )
    if (pick_modal := _MODAL_FOR_BUTTON.get(button.action)) is not None:
        view = pick_modal.view(
            PickModalContext(
                request_id=button.fingerprint,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                message=message,
            )
        )
        if not interaction.trigger_id or not await open_slack_modal(
            interaction.trigger_id, view_payload(view)
        ):
            logger.warning("Could not open a reviewer pick modal", extra=extra)
            return ignored("Could not open pick modal")
        return accepted("Pick modal opened")
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
    )
    return accepted("Human review click queued")
