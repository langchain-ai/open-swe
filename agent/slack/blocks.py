"""Typed Block Kit payloads Open SWE sends to Slack.

Only the surfaces Open SWE actually builds are declared. Each ``TypedDict``
mirrors one Block Kit object, so a malformed card is a type error here rather
than a ``invalid_blocks`` error from Slack at runtime.

Slack's own limits are enforced by the builders: section and context text is
truncated to :data:`SECTION_TEXT_MAX_CHARS`, because Slack rejects the whole
message when one block is over the limit.

``block_payload`` is the single boundary where typed blocks widen to the plain
dictionaries the Slack SDK takes; every other module should pass ``Block``
values around instead of raw dictionaries.
"""

from collections.abc import Sequence
from typing import Any, Literal, NotRequired, TypedDict, cast

SECTION_TEXT_MAX_CHARS = 3000
BUTTON_TEXT_MAX_CHARS = 75
ButtonStyle = Literal["primary", "danger"]


class PlainText(TypedDict):
    type: Literal["plain_text"]
    text: str
    emoji: NotRequired[bool]


class Mrkdwn(TypedDict):
    type: Literal["mrkdwn"]
    text: str


type TextObject = PlainText | Mrkdwn


class SectionBlock(TypedDict):
    type: Literal["section"]
    text: TextObject


class ContextBlock(TypedDict):
    type: Literal["context"]
    elements: list[TextObject]


class DividerBlock(TypedDict):
    type: Literal["divider"]


class SlackFileRef(TypedDict):
    id: str


class ImageBlock(TypedDict):
    type: Literal["image"]
    slack_file: SlackFileRef
    alt_text: str


class ButtonElement(TypedDict):
    type: Literal["button"]
    text: PlainText
    action_id: str
    value: NotRequired[str]
    style: NotRequired[ButtonStyle]


class ActionsBlock(TypedDict):
    type: Literal["actions"]
    elements: list[ButtonElement]


class FeedbackButton(TypedDict):
    text: PlainText
    value: str
    accessibility_label: str


class FeedbackButtons(TypedDict):
    type: Literal["feedback_buttons"]
    action_id: str
    positive_button: FeedbackButton
    negative_button: FeedbackButton


class ContextActionsBlock(TypedDict):
    type: Literal["context_actions"]
    elements: list[FeedbackButtons]


class PlainTextInput(TypedDict):
    type: Literal["plain_text_input"]
    action_id: str
    multiline: NotRequired[bool]
    max_length: NotRequired[int]
    placeholder: NotRequired[PlainText]


class CheckboxOption(TypedDict):
    text: PlainText
    value: str


class CheckboxesElement(TypedDict):
    type: Literal["checkboxes"]
    action_id: str
    options: list[CheckboxOption]


class InputBlock(TypedDict):
    type: Literal["input"]
    block_id: str
    label: PlainText
    element: PlainTextInput | CheckboxesElement
    optional: NotRequired[bool]


type Block = (
    SectionBlock
    | ContextBlock
    | DividerBlock
    | ImageBlock
    | ActionsBlock
    | ContextActionsBlock
    | InputBlock
)


class ModalView(TypedDict):
    type: Literal["modal"]
    callback_id: str
    title: PlainText
    blocks: list[Block]
    submit: NotRequired[PlainText]
    close: NotRequired[PlainText]
    private_metadata: NotRequired[str]


def plain_text(text: str, *, emoji: bool = True) -> PlainText:
    return {"type": "plain_text", "text": text, "emoji": emoji}


def mrkdwn(text: str) -> Mrkdwn:
    return {"type": "mrkdwn", "text": text[:SECTION_TEXT_MAX_CHARS]}


def section(text: str) -> SectionBlock:
    return {"type": "section", "text": mrkdwn(text)}


def context(*texts: str) -> ContextBlock:
    return {"type": "context", "elements": [mrkdwn(text) for text in texts]}


def image(file_id: str, alt_text: str) -> ImageBlock:
    return {"type": "image", "slack_file": {"id": file_id}, "alt_text": alt_text}


def divider() -> DividerBlock:
    return {"type": "divider"}


def button(
    text: str, *, action_id: str, value: str | None = None, style: ButtonStyle | None = None
) -> ButtonElement:
    element: ButtonElement = {
        "type": "button",
        "text": plain_text(text[:BUTTON_TEXT_MAX_CHARS]),
        "action_id": action_id,
    }
    if value is not None:
        element["value"] = value
    if style is not None:
        element["style"] = style
    return element


def actions(*elements: ButtonElement) -> ActionsBlock:
    return {"type": "actions", "elements": list(elements)}


def checkboxes(
    *, block_id: str, label: str, action_id: str, options: Sequence[str], optional: bool = False
) -> InputBlock:
    block: InputBlock = {
        "type": "input",
        "block_id": block_id,
        "label": plain_text(label),
        "element": {
            "type": "checkboxes",
            "action_id": action_id,
            "options": [
                {"text": plain_text(option[:75]), "value": str(index)}
                for index, option in enumerate(options)
            ],
        },
    }
    if optional:
        block["optional"] = True
    return block


def text_input(
    *,
    block_id: str,
    label: str,
    action_id: str,
    multiline: bool = False,
    max_length: int | None = None,
    placeholder: str | None = None,
    optional: bool = False,
) -> InputBlock:
    element: PlainTextInput | CheckboxesElement = {
        "type": "plain_text_input",
        "action_id": action_id,
    }
    if multiline:
        element["multiline"] = True
    if max_length is not None:
        element["max_length"] = max_length
    if placeholder is not None:
        element["placeholder"] = plain_text(placeholder)
    block: InputBlock = {
        "type": "input",
        "block_id": block_id,
        "label": plain_text(label),
        "element": element,
    }
    if optional:
        block["optional"] = True
    return block


def modal(
    *,
    callback_id: str,
    title: str,
    blocks: Sequence[Block],
    submit: str | None = None,
    close: str | None = None,
    private_metadata: str | None = None,
) -> ModalView:
    view: ModalView = {
        "type": "modal",
        "callback_id": callback_id,
        "title": plain_text(title),
        "blocks": list(blocks),
    }
    if submit is not None:
        view["submit"] = plain_text(submit)
    if close is not None:
        view["close"] = plain_text(close)
    if private_metadata is not None:
        view["private_metadata"] = private_metadata
    return view


def escape(value: str) -> str:
    """Escape the three characters Slack treats as markup in text objects."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def code_block(body: str, *, limit: int = SECTION_TEXT_MAX_CHARS) -> str:
    """``body`` fenced for a section, escaped and truncated to fit ``limit``."""
    fenced = escape(body).replace("```", "` ` `")
    budget = limit - len("```\n\n```") - 2
    if len(fenced) > budget:
        fenced = fenced[:budget].rstrip() + "\n…"
    return f"```\n{fenced}\n```"


def block_payload(blocks: Sequence[Block]) -> list[dict[str, Any]]:
    """Typed blocks as the Slack SDK wants them.

    The one place Block Kit values widen to untyped dictionaries: the SDK and
    the message helpers in :mod:`agent.slack.client` take plain dictionaries,
    so callers build ``Block`` values and convert once, here.
    """
    return [cast(dict[str, Any], block) for block in blocks]


def view_payload(view: ModalView) -> dict[str, Any]:
    """A typed modal view as the Slack SDK wants it. See :func:`block_payload`."""
    return cast(dict[str, Any], view)
