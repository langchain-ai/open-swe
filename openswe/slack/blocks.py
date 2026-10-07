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

import json
from collections.abc import Sequence
from typing import Any, Literal, NotRequired, TypedDict, cast

SECTION_TEXT_MAX_CHARS = 3000
# Slack documents no rich-text limit; 4.5k characters is the largest block verified to render.
CODE_TEXT_MAX_CHARS = 4000
# Cumulative across every markdown block in one message.
MARKDOWN_TEXT_MAX_CHARS = 12000
MESSAGE_MAX_BLOCKS = 50
BUTTON_TEXT_MAX_CHARS = 75
OPTION_TEXT_MAX_CHARS = 75
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


class MarkdownBlock(TypedDict):
    type: Literal["markdown"]
    text: str


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


class RichTextText(TypedDict):
    type: Literal["text"]
    text: str


class RichTextPreformatted(TypedDict):
    type: Literal["rich_text_preformatted"]
    elements: list[RichTextText]
    language: NotRequired[str]


class RichTextBlock(TypedDict):
    type: Literal["rich_text"]
    elements: list[RichTextPreformatted]


class ButtonElement(TypedDict):
    type: Literal["button"]
    text: PlainText
    action_id: str
    value: NotRequired[str]
    url: NotRequired[str]
    style: NotRequired[ButtonStyle]


class SelectOption(TypedDict):
    text: PlainText
    value: str


class StaticSelect(TypedDict):
    type: Literal["static_select"]
    action_id: str
    options: list[SelectOption]
    initial_option: NotRequired[SelectOption]
    placeholder: NotRequired[PlainText]


type ConversationKind = Literal["public", "private", "im", "mpim"]


class ConversationFilter(TypedDict, total=False):
    include: list[ConversationKind]
    exclude_external_shared_channels: bool
    exclude_bot_users: bool


class ConversationsSelect(TypedDict):
    type: Literal["conversations_select"]
    action_id: str
    filter: NotRequired[ConversationFilter]
    placeholder: NotRequired[PlainText]


type ActionElement = ButtonElement | StaticSelect


class ActionsBlock(TypedDict):
    type: Literal["actions"]
    elements: list[ActionElement]
    block_id: NotRequired[str]


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


class InputBlock(TypedDict):
    type: Literal["input"]
    block_id: str
    label: PlainText
    element: PlainTextInput | ConversationsSelect
    optional: NotRequired[bool]


type Block = (
    SectionBlock
    | MarkdownBlock
    | ContextBlock
    | DividerBlock
    | ImageBlock
    | RichTextBlock
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


def markdown(text: str) -> MarkdownBlock:
    return {"type": "markdown", "text": text}


def context(*texts: str) -> ContextBlock:
    return {"type": "context", "elements": [mrkdwn(text) for text in texts]}


def image(file_id: str, alt_text: str) -> ImageBlock:
    return {"type": "image", "slack_file": {"id": file_id}, "alt_text": alt_text}


def split_lines(text: str, limit: int) -> list[str]:
    """``text`` cut on line boundaries into non-blank pieces of at most ``limit`` characters."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    chunks.append(current)
    return [chunk.rstrip("\n") for chunk in chunks if chunk.strip()]


def code_blocks(body: str, *, language: str | None = None) -> list[RichTextBlock]:
    """All of ``body`` as syntax-highlighted code; unlike mrkdwn, rich text takes it literally."""
    blocks: list[RichTextBlock] = []
    for chunk in split_lines(body, CODE_TEXT_MAX_CHARS):
        preformatted: RichTextPreformatted = {
            "type": "rich_text_preformatted",
            "elements": [{"type": "text", "text": chunk}],
        }
        if language:
            preformatted["language"] = language
        blocks.append({"type": "rich_text", "elements": [preformatted]})
    return blocks


def divider() -> DividerBlock:
    return {"type": "divider"}


def button(
    text: str,
    *,
    action_id: str,
    value: str | None = None,
    url: str | None = None,
    style: ButtonStyle | None = None,
) -> ButtonElement:
    element: ButtonElement = {
        "type": "button",
        "text": plain_text(text[:BUTTON_TEXT_MAX_CHARS]),
        "action_id": action_id,
    }
    if value is not None:
        element["value"] = value
    if url is not None:
        element["url"] = url
    if style is not None:
        element["style"] = style
    return element


def actions(*elements: ActionElement, block_id: str | None = None) -> ActionsBlock:
    block: ActionsBlock = {"type": "actions", "elements": list(elements)}
    if block_id is not None:
        block["block_id"] = block_id
    return block


def option(text: str, value: str) -> SelectOption:
    return {"text": plain_text(text[:OPTION_TEXT_MAX_CHARS]), "value": value}


def static_select(
    *,
    action_id: str,
    options: Sequence[SelectOption],
    initial: SelectOption | None = None,
    placeholder: str | None = None,
) -> StaticSelect:
    element: StaticSelect = {
        "type": "static_select",
        "action_id": action_id,
        "options": list(options),
    }
    if initial is not None:
        element["initial_option"] = initial
    if placeholder is not None:
        element["placeholder"] = plain_text(placeholder)
    return element


def conversation_input(
    *, block_id: str, label: str, action_id: str, include: Sequence[ConversationKind]
) -> InputBlock:
    """A required channel picker for a modal; external shared channels are never offered."""
    return {
        "type": "input",
        "block_id": block_id,
        "label": plain_text(label),
        "element": {
            "type": "conversations_select",
            "action_id": action_id,
            "filter": {
                "include": list(include),
                "exclude_external_shared_channels": True,
                "exclude_bot_users": True,
            },
        },
    }


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
    element: PlainTextInput = {"type": "plain_text_input", "action_id": action_id}
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
    the message helpers in :mod:`openswe.slack.client` take plain dictionaries,
    so callers build ``Block`` values and convert once, here.
    """
    return [cast(dict[str, Any], block) for block in blocks]


def option_actions(options: list[str] | None) -> list[dict[str, Any]]:
    """Up to five option buttons; a click arrives as a message carrying the option's text."""
    clean_options = [option.strip() for option in options or [] if option.strip()]
    if not clean_options:
        return []
    return [
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": option[:75], "emoji": True},
                    "value": json.dumps({"type": "open_swe_option", "response": option}),
                    "action_id": f"open_swe_option_select_{index}",
                }
                for index, option in enumerate(clean_options[:5])
            ],
        }
    ]


def view_payload(view: ModalView) -> dict[str, Any]:
    """A typed modal view as the Slack SDK wants it. See :func:`block_payload`."""
    return cast(dict[str, Any], view)
