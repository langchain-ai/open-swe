"""Typed construction and serialization for application-owned model inputs."""

from html import escape
from typing import Any, Literal, NotRequired, TypedDict
from xml.etree import ElementTree

from langchain_core.messages import BaseMessage

INTRODUCED_ENTITY_IDS_KEY = "introduced_entity_ids"

Surface = Literal["slack", "linear", "github", "web", "desktop", "automation", "eval"]
EntityKind = Literal["person", "channel", "system"]
MessageKind = Literal["human", "system"]


class PersonIdentity(TypedDict):
    id: str
    display_name: NotRequired[str]
    handle: NotRequired[str]
    platform: NotRequired[str]
    github_login: NotRequired[str]
    email: NotRequired[str]
    timezone: NotRequired[str]


class ChannelIdentity(TypedDict):
    id: str
    platform: str
    name: NotRequired[str]
    thread_id: NotRequired[str]
    topic: NotRequired[str]
    purpose: NotRequired[str]


class SystemIdentity(TypedDict):
    id: str
    display_name: str
    platform: NotRequired[str]


Identity = PersonIdentity | ChannelIdentity | SystemIdentity


class InputMessageContext(TypedDict):
    sender_id: str
    surface: Surface
    kind: MessageKind
    channel_id: NotRequired[str]
    data: NotRequired[dict[str, object]]


class TextContentBlock(TypedDict):
    type: Literal["text"]
    text: str


class RunMessage(TypedDict):
    role: Literal["user", "system"]
    content: str | list[dict[str, Any]]


class RunInput(TypedDict):
    messages: list[RunMessage]
    files: NotRequired[dict[str, Any]]


_ENTITY_FIELDS: dict[EntityKind, tuple[str, ...]] = {
    "person": ("display_name", "handle", "platform", "github_login", "email", "timezone"),
    "channel": ("platform", "name", "thread_id", "topic", "purpose"),
    "system": ("display_name", "platform"),
}
_UNTRUSTED_ENTITY_FIELDS = frozenset({"topic", "purpose"})
_SYSTEM_ENTITY_ID = "system:open-swe"
_SYSTEM_WRAPPER_MARKER = '<chat_system format="open-swe-v1">'


def _xml_text(value: object) -> str:
    return escape(str(value), quote=True)


def _validate_entity_id(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not entity_id.strip() or ":" not in entity_id:
        raise ValueError("entity id must be a non-empty namespaced identifier")
    if any(char.isspace() or char in "<>\"'" for char in entity_id):
        raise ValueError("entity id contains invalid characters")
    return entity_id


def introduced_entity_ids_from_metadata(metadata: object) -> set[str]:
    if not isinstance(metadata, dict):
        return set()
    values = metadata.get(INTRODUCED_ENTITY_IDS_KEY)
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str) and value}


def message_sender_id(content: object) -> str | None:
    values = content if isinstance(content, list) else [content]
    for value in values:
        text = value.get("text") if isinstance(value, dict) else value
        if not isinstance(text, str) or "<chat_message" not in text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        messages = [root] if root.tag == "chat_message" else root.findall(".//chat_message")
        for message in messages:
            sender = message.get("sender")
            if sender:
                return sender
    return None


def entity_ids_from_messages(messages: object) -> set[str]:
    if not isinstance(messages, (list, tuple)):
        return set()
    found: set[str] = set()
    for message in messages:
        content = message.content if isinstance(message, BaseMessage) else None
        if content is None and isinstance(message, dict):
            content = message.get("content")
        values = content if isinstance(content, list) else [content]
        for value in values:
            text = value.get("text") if isinstance(value, dict) else value
            if not isinstance(text, str) or "<chat_entity" not in text:
                continue
            try:
                root = ElementTree.fromstring(text)
            except ElementTree.ParseError:
                continue
            entities = [root] if root.tag == "chat_entity" else root.findall(".//chat_entity")
            for entity in entities:
                entity_id = entity.get("id")
                if entity_id:
                    found.add(entity_id)
    return found


def _entity_message(identity: Identity, kind: EntityKind) -> RunMessage:
    entity_id = _validate_entity_id(identity["id"])
    children: list[str] = []
    for field in _ENTITY_FIELDS[kind]:
        value = identity.get(field)  # type: ignore[union-attr]
        if value is None or value == "":
            continue
        trust = ' trust="untrusted"' if field in _UNTRUSTED_ENTITY_FIELDS else ""
        children.append(f"<{field}{trust}>{_xml_text(value)}</{field}>")
    body = "\n".join(children)
    serialized = f'<chat_entity kind="{kind}" id="{_xml_text(entity_id)}">'
    if body:
        serialized += f"\n{body}\n"
    serialized += "</chat_entity>"
    return {"role": "user", "content": serialized}


def person_introduction(person: PersonIdentity) -> RunMessage:
    return _entity_message(person, "person")


def channel_introduction(channel: ChannelIdentity) -> RunMessage:
    return _entity_message(channel, "channel")


def system_introduction(system: SystemIdentity) -> RunMessage:
    return _entity_message(system, "system")


def _data_element(name: str, value: object) -> str:
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid structured data field: {name}")
    if isinstance(value, dict):
        children = "\n".join(_data_element(str(key), item) for key, item in value.items())
        return f"<{name}>\n{children}\n</{name}>"
    if isinstance(value, (list, tuple)):
        children = "\n".join(_data_element("item", item) for item in value)
        return f"<{name}>\n{children}\n</{name}>"
    return f"<{name}>{_xml_text(value)}</{name}>"


def _serialize_message(text: str, context: InputMessageContext) -> str:
    sender_id = _validate_entity_id(context["sender_id"])
    attributes = [
        f'sender="{_xml_text(sender_id)}"',
        f'surface="{context["surface"]}"',
        f'kind="{context["kind"]}"',
    ]
    channel_id = context.get("channel_id")
    if channel_id:
        attributes.insert(1, f'channel="{_xml_text(_validate_entity_id(channel_id))}"')
    children = [_data_element(name, value) for name, value in context.get("data", {}).items()]
    children.append(f"<content>{_xml_text(text)}</content>")
    body = "\n".join(children)
    return f"<chat_message {' '.join(attributes)}>\n{body}\n</chat_message>"


def _structured_content(
    content: str | list[dict[str, Any]], context: InputMessageContext
) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return _serialize_message(content, context)
    blocks: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            blocks.append({**block, "text": _serialize_message(block["text"], context)})
        else:
            blocks.append(block)
    return blocks


def human_input(content: str | list[dict[str, Any]], context: InputMessageContext) -> RunMessage:
    if context["kind"] != "human":
        raise ValueError("human_input requires kind='human'")
    return {"role": "user", "content": _structured_content(content, context)}


def system_input(text: str, context: InputMessageContext) -> RunMessage:
    if context["kind"] != "system":
        raise ValueError("system_input requires kind='system'")
    return {"role": "user", "content": _serialize_message(text, context)}


def filter_new_entity_introductions(
    messages: list[RunMessage], introduced_entity_ids: set[str]
) -> tuple[list[RunMessage], set[str]]:
    filtered: list[RunMessage] = []
    newly_introduced: set[str] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str) or "<chat_entity" not in content:
            filtered.append(message)
            continue
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            filtered.append(message)
            continue
        entity_id = root.get("id") if root.tag == "chat_entity" else None
        if not entity_id:
            filtered.append(message)
            continue
        if entity_id not in introduced_entity_ids:
            filtered.append(message)
            newly_introduced.add(entity_id)
    return filtered, newly_introduced


def build_input_messages(
    content: str | list[dict[str, Any]],
    context: InputMessageContext,
    *,
    people: list[PersonIdentity] | None = None,
    channels: list[ChannelIdentity] | None = None,
    systems: list[SystemIdentity] | None = None,
    introduced_entity_ids: set[str] | None = None,
) -> list[RunMessage]:
    introduced = introduced_entity_ids if introduced_entity_ids is not None else set()
    messages: list[RunMessage] = []
    for identity, builder in (
        *((person, person_introduction) for person in people or []),
        *((channel, channel_introduction) for channel in channels or []),
        *((system, system_introduction) for system in systems or []),
    ):
        entity_id = _validate_entity_id(identity["id"])
        if entity_id in introduced:
            continue
        messages.append(builder(identity))  # type: ignore[arg-type]
        introduced.add(entity_id)
    if context["kind"] == "human":
        messages.append(human_input(content, context))
    else:
        if not isinstance(content, str):
            raise ValueError("system inputs must contain text")
        messages.append(system_input(content, context))
    return messages


def build_run_input(
    content: str | list[dict[str, Any]],
    context: InputMessageContext,
    *,
    people: list[PersonIdentity] | None = None,
    channels: list[ChannelIdentity] | None = None,
    systems: list[SystemIdentity] | None = None,
    introduced_entity_ids: set[str] | None = None,
    files: dict[str, Any] | None = None,
) -> RunInput:
    result: RunInput = {
        "messages": build_input_messages(
            content,
            context,
            people=people,
            channels=channels,
            systems=systems,
            introduced_entity_ids=introduced_entity_ids,
        )
    }
    if files is not None:
        result["files"] = files
    return result


def wrap_system_prompt(text: str, *, additions: list[str] | None = None) -> str:
    if text.startswith(_SYSTEM_WRAPPER_MARKER) and text.endswith("</chat_system>"):
        if not additions:
            return text
        closing = "</chat_system>"
        serialized_additions = [
            _serialize_message(
                addition,
                {"sender_id": _SYSTEM_ENTITY_ID, "surface": "automation", "kind": "system"},
            )
            for addition in additions
        ]
        extra = "\n".join(item for item in serialized_additions if item not in text)
        if not extra:
            return text
        return f"{text[: -len(closing)]}{extra}\n{closing}"
    identity = system_introduction(
        {"id": _SYSTEM_ENTITY_ID, "display_name": "Open SWE", "platform": "open-swe"}
    )["content"]
    message = _serialize_message(
        text,
        {"sender_id": _SYSTEM_ENTITY_ID, "surface": "automation", "kind": "system"},
    )
    extras = [
        _serialize_message(
            addition,
            {"sender_id": _SYSTEM_ENTITY_ID, "surface": "automation", "kind": "system"},
        )
        for addition in additions or []
    ]
    return "\n".join([_SYSTEM_WRAPPER_MARKER, str(identity), message, *extras, "</chat_system>"])
