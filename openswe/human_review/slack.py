"""Slack interactivity for human review cards and picks: I'll review, Accept, Decline, Snooze, Dismiss, Merge, and Undo auto-approve."""

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import BackgroundTasks
from pydantic import BaseModel, ValidationError

from openswe.human_review import card
from openswe.human_review.clicks import answer_click
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.people import Outcome
from openswe.human_review.pick_message import PickMessage
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import (
    SNOOZE_DURATIONS,
    claim,
    decline,
    merge_now,
    snooze,
    undo_auto_approval,
)
from openswe.prompts import prompt
from openswe.slack.blocks import (
    InputBlock,
    ModalView,
    modal,
    option,
    plain_text,
    static_select,
    text_input,
    view_payload,
)
from openswe.slack.client import open_slack_modal
from openswe.slack.dm import note_for_concierge
from openswe.slack.payloads import SlackButtonValue, SlackInteraction, SlackViewState
from openswe.slack.responses import FeedbackResponse, WebhookResponse, accepted, ignored
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.users import User

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
_ANSWER_MAX_CHARS = 1000


@dataclass(frozen=True, slots=True)
class ClickModal:
    """A button that asks one required question in a modal before acting.

    The answer is one of ``choices``, or free text when there are none.
    """

    action: Literal["decline", "snooze", "undo_auto_approve"]
    callback_id: str
    title: str
    question: str
    placeholder: str
    submit: str
    choices: tuple[str, ...] = ()
    initial: str | None = None

    def _field(self) -> InputBlock:
        if not self.choices:
            return text_input(
                block_id=_CHOICE,
                label=self.question,
                action_id=_CHOICE,
                multiline=True,
                max_length=_ANSWER_MAX_CHARS,
                placeholder=self.placeholder,
            )
        select = static_select(
            action_id=_CHOICE,
            options=[option(choice, choice) for choice in self.choices],
            initial=option(self.initial, self.initial) if self.initial else None,
            placeholder=self.placeholder,
        )
        return {
            "type": "input",
            "block_id": _CHOICE,
            "label": plain_text(self.question),
            "element": select,
        }

    def answer(self, state: SlackViewState) -> str | None:
        """The submitted answer, or ``None`` when it is missing or not an offered choice."""
        value = state.input(_CHOICE, _CHOICE)
        if not self.choices:
            return " ".join((value.value or "").split())[:_ANSWER_MAX_CHARS] or None
        selected = value.selected_option
        return selected.value if selected is not None and selected.value in self.choices else None

    def view(self, context: ClickModalContext) -> ModalView:
        return modal(
            callback_id=self.callback_id,
            title=self.title,
            blocks=[self._field()],
            submit=self.submit,
            close="Cancel",
            private_metadata=context.model_dump_json(),
        )


_PENDING = {
    "review": ":hourglass_flowing_sand: Accepting…",
    "decline": ":hourglass_flowing_sand: Declining…",
    "snooze": ":hourglass_flowing_sand: Snoozing…",
}


class ClickModalContext(BaseModel):
    request_id: str
    channel_id: str
    thread_ts: str
    user_id: str
    message: PickMessage | None = None


CLICK_MODALS = {
    click_modal.callback_id: click_modal
    for click_modal in (
        ClickModal(
            action="decline",
            callback_id="human_review_decline",
            title="Decline review",
            question="Why are you declining?",
            placeholder="Select a reason",
            submit="Decline",
            choices=DECLINE_REASONS,
        ),
        ClickModal(
            action="snooze",
            callback_id="human_review_snooze",
            title="Snooze review",
            question="Remind me in",
            placeholder="Select how long",
            submit="Snooze",
            choices=tuple(SNOOZE_DURATIONS),
            initial="1 hour",
        ),
        ClickModal(
            action="undo_auto_approve",
            callback_id="human_review_undo_auto_approve",
            title="Undo auto-approve",
            question="Why should this not be auto-approved?",
            placeholder="Shown on the card and the GitHub dismissal",
            submit="Undo",
        ),
    )
}
_MODAL_FOR_BUTTON = {click_modal.action: click_modal for click_modal in CLICK_MODALS.values()}


async def handle_modal_submission(
    interaction: SlackInteraction, background_tasks: BackgroundTasks
) -> FeedbackResponse | WebhookResponse:
    click_modal = CLICK_MODALS[interaction.view.callback_id]
    try:
        context = ClickModalContext.model_validate_json(interaction.view.private_metadata)
    except ValidationError:
        logger.warning("Invalid reviewer pick modal context", exc_info=True)
        return ignored("Invalid pick modal context")
    answer = click_modal.answer(interaction.view.state)
    if context.user_id != interaction.user.id or answer is None:
        return ignored("Invalid pick modal submission")
    background_tasks.add_task(
        _process,
        context.request_id,
        click_modal.action,
        channel_id=context.channel_id,
        thread_ts=context.thread_ts,
        slack_user_id=interaction.user.id,
        choice=answer,
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
            outcome = await ReviewCard(request).dismiss(slack_user_id)
        else:
            user = await User.for_person({"id": f"slack:{slack_user_id}"})
            if action == "merge":
                outcome = await merge_now(request, user)
            elif action == "undo_auto_approve":
                outcome = await undo_auto_approval(request, user, choice)
            elif action == "decline":
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
    if (click_modal := _MODAL_FOR_BUTTON.get(button.action)) is not None:
        view = click_modal.view(
            ClickModalContext(
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
    if button.action not in {"review", "dismiss", "merge"}:
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
