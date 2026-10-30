"""The ``<input-message>`` envelope a channel's run input is written in, as the backend writes it.

A copy of the part of ``openswe.input_messages`` the channels here use: ``mda/``
never imports the backend (AGENTS.md's exception for channel input). The model
tells messages and context blocks apart by their text, and the backend dedupes
context blocks by it, so this writes byte for byte what the backend does:
``tests/agent/test_mda_input_messages.py`` checks it against the backend's.
Standard library only, so that test can load it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import Literal, NotRequired, TypedDict

type FieldValue = str | int | float | bool
type FieldValues = Mapping[str, FieldValue | None]
type EnvelopeData = Mapping[str, FieldValue | FieldValues | list[FieldValues]]
type Content = str | list[dict[str, object]]


class SystemIdentity(TypedDict):
    id: str
    display_name: str
    platform: NotRequired[str]
    sender_type: NotRequired[str]
    content: NotRequired[str]


class InputMessageContext(TypedDict):
    sender_id: str
    surface: Literal["linear"]
    kind: Literal["human", "system"]
    channel_id: NotRequired[str]
    data: NotRequired[EnvelopeData]


class RunMessage(TypedDict):
    role: Literal["user"]
    content: Content
    additional_kwargs: NotRequired[dict[str, object]]


class RunInput(TypedDict):
    messages: list[RunMessage]


_SYSTEM_FIELDS = ("display_name", "platform", "sender_type", "content")


def _xml_text(value: object) -> str:
    return escape(str(value), quote=False)


def _xml_attr(value: object) -> str:
    return escape(str(value), quote=True)


def _validate_entity_id(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not entity_id.strip() or ":" not in entity_id:
        raise ValueError("entity id must be a non-empty namespaced identifier")
    if any(char.isspace() or char in "<>\"'" for char in entity_id):
        raise ValueError("entity id contains invalid characters")
    return entity_id


def _validate_name(name: str) -> str:
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid structured data field: {name}")
    return name


@dataclass(frozen=True)
class Fields:
    """Structured data as the model reads it: one ``name: value`` line per field."""

    values: FieldValues

    def element(self, tag: str, attributes: Mapping[str, str] | None = None) -> str:
        opening = " ".join(
            [
                _validate_name(tag),
                *(f'{_validate_name(k)}="{_xml_attr(v)}"' for k, v in (attributes or {}).items()),
            ]
        )
        body = self._lines()
        return f"<{opening}>\n{body}\n</{tag}>" if body else f"<{opening}></{tag}>"

    def _lines(self) -> str:
        lines: list[str] = []
        for name, value in self.values.items():
            if value is None or value == "":
                continue
            text = _xml_text(value)
            if "\n" in text:
                indented = "\n".join(f"  {line}" for line in text.split("\n"))
                lines.append(f"{name}:\n{indented}")
            else:
                lines.append(f"{name}: {text}")
        return "\n".join(lines)


def system_introduction(system: SystemIdentity) -> RunMessage:
    entity_id = _validate_entity_id(system["id"])
    fields = Fields({field: system.get(field) for field in _SYSTEM_FIELDS})
    return {
        "role": "user",
        "content": fields.element("dynamic-context", {"kind": "system", "id": entity_id}),
    }


def _serialize_message(text: str, context: InputMessageContext) -> str:
    sender_id = _validate_entity_id(context["sender_id"])
    attributes = [
        f'sender="{_xml_attr(sender_id)}"',
        f'surface="{context["surface"]}"',
        f'kind="{context["kind"]}"',
    ]
    channel_id = context.get("channel_id")
    if channel_id:
        attributes.insert(1, f'channel="{_xml_attr(_validate_entity_id(channel_id))}"')
    children: list[str] = []
    for name, value in context.get("data", {}).items():
        if isinstance(value, Mapping):
            children.append(Fields(value).element(name))
        elif isinstance(value, list):
            children.extend(Fields(item).element(name) for item in value)
        else:
            attributes.append(f'{_validate_name(name)}="{_xml_attr(value)}"')
    body = "\n".join([_xml_text(text), *children])
    return f"<input-message {' '.join(attributes)}>\n{body}\n</input-message>"


def _structured_content(content: Content, context: InputMessageContext) -> Content:
    if isinstance(content, str):
        return _serialize_message(content, context)
    blocks: list[dict[str, object]] = []
    for block in content:
        text = block.get("text")
        if block.get("type") == "text" and isinstance(text, str):
            blocks.append({**block, "text": _serialize_message(text, context)})
        else:
            blocks.append(block)
    # Data elements live in the envelope, so a message without text still needs one.
    carries_elements = any(
        isinstance(value, Mapping | list) for value in context.get("data", {}).values()
    )
    if carries_elements and not any(block.get("type") == "text" for block in content):
        blocks.append({"type": "text", "text": _serialize_message("", context)})
    return blocks


def human_input(content: Content, context: InputMessageContext) -> RunMessage:
    if context["kind"] != "human":
        raise ValueError("human_input requires kind='human'")
    return {"role": "user", "content": _structured_content(content, context)}


def system_input(content: Content, context: InputMessageContext) -> RunMessage:
    if context["kind"] != "system":
        raise ValueError("system_input requires kind='system'")
    # LangChain's notification metadata (``add_notification_metadata``), which marks
    # the message as one no person typed; chat models don't send it to the model.
    return {
        "role": "user",
        "content": _structured_content(content, context),
        "additional_kwargs": {"lc_source": context["sender_id"]},
    }
