"""The ``<input-message>`` envelope a channel's run input is written in, as the backend writes it.

A copy of the part of ``openswe.input_messages`` the channels here use: ``mda/``
never imports the backend (AGENTS.md's exception for channel input). The model
tells messages and context blocks apart by their text, and the backend dedupes
context blocks by it, so this writes byte for byte what the backend does:
``tests/agent/test_mda_input_messages.py`` checks it against the backend's.
Standard library only, so that test can load it.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import Literal, NotRequired, TypedDict, cast
from xml.etree import ElementTree

from runtime_api.provenance import Provenance

type FieldValue = str | int | float | bool
type FieldValues = Mapping[str, FieldValue | None]
type EnvelopeData = Mapping[str, FieldValue | FieldValues | list[FieldValues]]
type Content = str | list[dict[str, object]]


class PersonIdentity(TypedDict):
    """Everything the agent knows about one person, whichever surface they wrote on."""

    id: str
    display_name: NotRequired[str]
    github_login: NotRequired[str]
    slack_user_id: NotRequired[str]
    commit_name: NotRequired[str]
    commit_email: NotRequired[str]
    email: NotRequired[str]
    timezone: NotRequired[str]
    open_swe_account: NotRequired[Literal["linked", "unlinked"]]
    workspace_admin: NotRequired[Literal["yes", "no"]]
    new_prs: NotRequired[Literal["as drafts", "ready for review"]]
    standing_instructions: NotRequired[str]


class ChannelIdentity(TypedDict):
    """The conversation a message arrived in, with whatever stays true of it."""

    id: str
    platform: str
    name: NotRequired[str]
    thread_id: NotRequired[str]
    topic: NotRequired[str]
    purpose: NotRequired[str]
    description: NotRequired[str]
    default_repo: NotRequired[str]
    web_url: NotRequired[str]
    trace_url: NotRequired[str]


class SystemIdentity(TypedDict):
    id: str
    display_name: str
    platform: NotRequired[str]
    sender_type: NotRequired[str]
    content: NotRequired[str]


class InputMessageContext(TypedDict):
    sender_id: str
    surface: Literal["github", "linear", "slack", "web"]
    kind: Literal["human", "system"]
    channel_id: NotRequired[str]
    data: NotRequired[EnvelopeData]


class RunMessage(TypedDict):
    role: Literal["user"]
    content: Content
    additional_kwargs: NotRequired[dict[str, object]]


class RunInput(TypedDict):
    messages: list[RunMessage]


_PERSON_FIELDS = (
    "display_name",
    "github_login",
    "slack_user_id",
    "commit_name",
    "commit_email",
    "email",
    "timezone",
    "open_swe_account",
    "workspace_admin",
    "new_prs",
    "standing_instructions",
)
_CHANNEL_FIELDS = (
    "platform",
    "name",
    "thread_id",
    "topic",
    "purpose",
    "description",
    "default_repo",
    "web_url",
    "trace_url",
)
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


def _entity_message(
    identity: Mapping[str, object],
    kind: Literal["person", "channel", "system"],
    fields: tuple[str, ...],
) -> RunMessage:
    entity_id = _validate_entity_id(str(identity["id"]))
    # Every identity field is a string, so each is a field value.
    values = Fields({field: cast("str | None", identity.get(field)) for field in fields})
    return {
        "role": "user",
        "content": values.element("dynamic-context", {"kind": kind, "id": entity_id}),
        "additional_kwargs": Provenance(kind="context", sender=entity_id).stamp(),
    }


def person_introduction(person: PersonIdentity) -> RunMessage:
    return _entity_message(person, "person", _PERSON_FIELDS)


def channel_introduction(channel: ChannelIdentity) -> RunMessage:
    return _entity_message(channel, "channel", _CHANNEL_FIELDS)


def system_introduction(system: SystemIdentity) -> RunMessage:
    return _entity_message(system, "system", _SYSTEM_FIELDS)


def _content_texts(content: object) -> list[str]:
    values = content if isinstance(content, list) else [content]
    return [
        text
        for value in values
        if isinstance(text := (value.get("text") if isinstance(value, dict) else value), str)
    ]


def _input_message_elements(content: object) -> list[ElementTree.Element]:
    elements: list[ElementTree.Element] = []
    for text in _content_texts(content):
        if "<input-message" not in text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        elements.extend([root] if root.tag == "input-message" else root.findall(".//input-message"))
    return elements


def _envelope_body(message: ElementTree.Element) -> str:
    """The authored text of an envelope, accepting the stored ``<content>`` shape."""
    return (message.text or "").strip() or (message.findtext("content") or "").strip()


def input_message_text(content: object) -> str | None:
    """The authored text carried by a serialized input message, when present."""
    texts = [
        body for message in _input_message_elements(content) if (body := _envelope_body(message))
    ]
    return "\n\n".join(texts) or None


def input_message_timestamps(content: object) -> set[str]:
    """Source-message timestamps the serialized envelopes in ``content`` carry."""
    return {
        timestamp
        for message in _input_message_elements(content)
        if (timestamp := message.get("timestamp"))
    }


def _dynamic_context(content: object) -> tuple[tuple[str, str], str] | None:
    values = content if isinstance(content, list) else [content]
    for value in values:
        text = value.get("text") if isinstance(value, dict) else value
        if not isinstance(text, str) or "<dynamic-context" not in text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        if root.tag != "dynamic-context":
            continue
        claimed_hash = root.attrib.pop("hash", None)
        canonical = ElementTree.tostring(root, encoding="unicode")
        context_hash = hashlib.sha256(canonical.encode()).hexdigest()
        if claimed_hash is None or claimed_hash == context_hash:
            key = (root.get("kind", ""), root.get("id") or context_hash)
            return key, context_hash
    return None


def dynamic_context_hash(content: object) -> str | None:
    """The hash the backend dedupes a context block by; ``None`` for any other content."""
    context = _dynamic_context(content)
    return context[1] if context else None


def dynamic_context_hashes_from_messages(messages: object) -> set[str]:
    """The hash of each context block's latest version among ``messages`` (JSON messages)."""
    if not isinstance(messages, list | tuple):
        return set()
    latest: dict[tuple[str, str], str] = {}
    for message in messages:
        content = message.get("content") if isinstance(message, Mapping) else None
        context = _dynamic_context(content)
        if context is not None:
            key, context_hash = context
            latest[key] = context_hash
    return set(latest.values())


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


def _provenance(context: InputMessageContext) -> Provenance:
    """What the message is, recorded on it as the backend's input builders record it."""
    return Provenance(
        kind=context["kind"],
        sender=context["sender_id"],
        surface=context["surface"],
        channel=context.get("channel_id"),
    )


def human_input(content: Content, context: InputMessageContext) -> RunMessage:
    if context["kind"] != "human":
        raise ValueError("human_input requires kind='human'")
    return {
        "role": "user",
        "content": _structured_content(content, context),
        "additional_kwargs": _provenance(context).stamp(),
    }


def system_input(content: Content, context: InputMessageContext) -> RunMessage:
    if context["kind"] != "system":
        raise ValueError("system_input requires kind='system'")
    # LangChain's notification metadata (``add_notification_metadata``), which marks
    # the message as one no person typed; chat models don't send it to the model.
    return {
        "role": "user",
        "content": _structured_content(content, context),
        "additional_kwargs": _provenance(context).stamp({"lc_source": context["sender_id"]}),
    }
