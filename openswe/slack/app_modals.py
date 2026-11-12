"""Modals Trigger Server opens for the app's own buttons.

A button whose ``action_id`` is ``mda_modal::<name>:<id>`` carries its view in its
``value``; Trigger Server opens it inside the click and sends the submission to MDA's
Slack channel with that ``action_id`` and the button's channel and thread. Where
Trigger Server refuses the click, the backend opens it itself (:func:`open_here`),
and its submission stays here (:func:`origin_here`).
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from openswe.slack.blocks import ButtonElement, ButtonStyle, ModalView, button, view_payload
from openswe.slack.client import open_slack_modal
from openswe.slack.payloads import SlackBlockAction, SlackInteraction
from openswe.slack.responses import WebhookResponse, accepted, ignored

PREFIX = "mda_modal::"
#: Slack's limit on a button's ``value``.
VALUE_LIMIT = 2000


def modal_button(
    text: str, *, name: str, id: str, view: ModalView, style: ButtonStyle | None = None
) -> ButtonElement:
    """A button that opens ``view``; its submission names ``name`` and ``id``."""
    spec = {
        key: value
        for key, value in view_payload(view).items()
        if key not in {"callback_id", "private_metadata"}
    }
    value = json.dumps(spec, separators=(",", ":"))
    if len(value) > VALUE_LIMIT:
        raise ValueError(f"The {name} modal is {len(value)} characters, over Slack's {VALUE_LIMIT}")
    return button(text, action_id=f"{PREFIX}{name}:{id}", value=value, style=style)


def modal_action(action_id: str) -> tuple[str, str] | None:
    """``(name, id)`` of a modal's button or submission, with or without the prefix."""
    name, sep, id = action_id.removeprefix(PREFIX).partition(":")
    return (name, id) if sep and name and id else None


class _HereOrigin(BaseModel):
    """``private_metadata`` of a modal the backend opened itself."""

    open_swe: Literal["app_modal"] = "app_modal"
    channel_id: str
    thread_ts: str


async def open_here(interaction: SlackInteraction, action: SlackBlockAction) -> WebhookResponse:
    """Open a modal button's view, as Trigger Server would have."""
    try:
        spec = json.loads(action.value or "")
    except ValueError:
        spec = None
    if not isinstance(spec, dict) or not interaction.trigger_id:
        return ignored("Invalid modal button")
    origin = _HereOrigin(channel_id=interaction.channel_id, thread_ts=interaction.thread_ts)
    view = {**spec, "callback_id": action.action_id, "private_metadata": origin.model_dump_json()}
    if not await open_slack_modal(interaction.trigger_id, view):
        return ignored("Could not open the modal")
    return accepted("Modal opened")


def origin_here(interaction: SlackInteraction) -> tuple[str, str] | None:
    """The channel and thread of a modal the backend opened itself; ``None`` for any other."""
    try:
        origin = _HereOrigin.model_validate_json(interaction.view.private_metadata or "{}")
    except ValidationError:
        return None
    return origin.channel_id, origin.thread_ts
