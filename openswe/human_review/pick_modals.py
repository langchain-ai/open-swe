"""The modals a reviewer pick's Decline and Snooze buttons open, through Trigger Server."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from openswe.slack.blocks import (
    ButtonElement,
    InputBlock,
    ModalView,
    modal,
    option,
    plain_text,
    static_select,
)

DECLINE_REASONS = (
    "Too many reviews / not enough time",
    "Away or unavailable",
    "Not familiar with this code",
    "Someone else is a better reviewer",
    "Conflict of interest",
    "Other",
)
SNOOZE_DURATIONS = {
    "30 minutes": timedelta(minutes=30),
    "1 hour": timedelta(hours=1),
    "2 hours": timedelta(hours=2),
    "4 hours": timedelta(hours=4),
    "1 day": timedelta(days=1),
    "2 days": timedelta(days=2),
}
CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class PickModal:
    """A pick's button that asks one required question in a modal before acting."""

    action: Literal["decline", "snooze"]
    callback_id: str
    label: str
    title: str
    question: str
    placeholder: str
    submit: str
    choices: tuple[str, ...]
    initial: str | None = None

    def view(self, private_metadata: str | None = None) -> ModalView:
        select = static_select(
            action_id=CHOICE,
            options=[option(choice, choice) for choice in self.choices],
            initial=option(self.initial, self.initial) if self.initial else None,
            placeholder=self.placeholder,
        )
        question: InputBlock = {
            "type": "input",
            "block_id": CHOICE,
            "label": plain_text(self.question),
            "element": select,
        }
        return modal(
            callback_id=self.callback_id,
            title=self.title,
            blocks=[question],
            submit=self.submit,
            close="Cancel",
            private_metadata=private_metadata,
        )

    def button(self, request_id: str) -> ButtonElement:
        from openswe.slack.app_modals import modal_button  # noqa: PLC0415 - slack imports cards

        return modal_button(self.label, name=self.callback_id, id=request_id, view=self.view())


PICK_MODALS = {
    pick_modal.callback_id: pick_modal
    for pick_modal in (
        PickModal(
            action="decline",
            callback_id="human_review_decline",
            label="Decline",
            title="Decline review",
            question="Why are you declining?",
            placeholder="Select a reason",
            submit="Decline",
            choices=DECLINE_REASONS,
        ),
        PickModal(
            action="snooze",
            callback_id="human_review_snooze",
            label="Snooze",
            title="Snooze review",
            question="Remind me in",
            placeholder="Select how long",
            submit="Snooze",
            choices=tuple(SNOOZE_DURATIONS),
            initial="1 hour",
        ),
    )
}
MODAL_FOR_BUTTON = {pick_modal.action: pick_modal for pick_modal in PICK_MODALS.values()}
