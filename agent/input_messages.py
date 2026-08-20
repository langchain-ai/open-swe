"""Typed construction and serialization for application-owned model inputs."""

import hashlib
from html import escape
from typing import Any, Literal, NotRequired, TypedDict, cast
from xml.etree import ElementTree

from langchain_core.messages import AnyMessage, BaseMessage

INJECTED_DYNAMIC_CONTEXT_HASHES_KEY = "injected_dynamic_context_hashes"

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
_SYSTEM_WRAPPER_MARKER = '<system-instructions format="open-swe-v1">'


def _xml_text(value: object) -> str:
    return escape(str(value), quote=True)


def _validate_entity_id(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not entity_id.strip() or ":" not in entity_id:
        raise ValueError("entity id must be a non-empty namespaced identifier")
    if any(char.isspace() or char in "<>\"'" for char in entity_id):
        raise ValueError("entity id contains invalid characters")
    return entity_id


def injected_dynamic_context_hashes_from_metadata(metadata: object) -> set[str]:
    if not isinstance(metadata, dict):
        return set()
    values = metadata.get(INJECTED_DYNAMIC_CONTEXT_HASHES_KEY)
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str) and value}


def message_sender_id(content: object) -> str | None:
    values = content if isinstance(content, list) else [content]
    for value in values:
        text = value.get("text") if isinstance(value, dict) else value
        if not isinstance(text, str) or "<input-message" not in text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        messages = [root] if root.tag == "input-message" else root.findall(".//input-message")
        for message in messages:
            sender = message.get("sender")
            if sender:
                return sender
    return None


def input_message_text(content: object) -> str | None:
    """The authored text carried by a serialized input message, when present."""
    texts: list[str] = []
    values = content if isinstance(content, list) else [content]
    for value in values:
        text = value.get("text") if isinstance(value, dict) else value
        if not isinstance(text, str) or "<input-message" not in text:
            continue
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            continue
        messages = [root] if root.tag == "input-message" else root.findall(".//input-message")
        for message in messages:
            body = message.findtext("content")
            if body and body.strip():
                texts.append(body.strip())
    return "\n\n".join(texts) or None


def dynamic_context_hash(content: object) -> str | None:
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
            return context_hash
    return None


def dynamic_context_messages(messages: object) -> list[AnyMessage]:
    if not isinstance(messages, (list, tuple)):
        return []
    found: list[AnyMessage] = []
    hashes: set[str] = set()
    for message in messages:
        if not isinstance(message, BaseMessage):
            continue
        message = cast(AnyMessage, message)
        context_hash = dynamic_context_hash(message.content)
        if context_hash is not None and context_hash not in hashes:
            hashes.add(context_hash)
            found.append(message)
    return found


def dynamic_context_hashes_from_messages(messages: object) -> set[str]:
    hashes: set[str] = set()
    for message in dynamic_context_messages(messages):
        context_hash = dynamic_context_hash(message.content)
        if context_hash is not None:
            hashes.add(context_hash)
    return hashes


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
    canonical = f'<dynamic-context kind="{kind}" id="{_xml_text(entity_id)}">'
    if body:
        canonical += f"\n{body}\n"
    canonical += "</dynamic-context>"
    context_hash = hashlib.sha256(canonical.encode()).hexdigest()
    serialized = canonical.replace(">", f' hash="{context_hash}">', 1)
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
    return f"<input-message {' '.join(attributes)}>\n{body}\n</input-message>"


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


def system_input(content: str | list[dict[str, Any]], context: InputMessageContext) -> RunMessage:
    if context["kind"] != "system":
        raise ValueError("system_input requires kind='system'")
    return {"role": "user", "content": _structured_content(content, context)}


def filter_new_dynamic_contexts(
    messages: list[RunMessage], injected_dynamic_context_hashes: set[str]
) -> tuple[list[RunMessage], set[str]]:
    filtered: list[RunMessage] = []
    newly_injected: set[str] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str) or "<dynamic-context" not in content:
            filtered.append(message)
            continue
        context_hash = dynamic_context_hash(content)
        if context_hash is None:
            filtered.append(message)
            continue
        if context_hash not in injected_dynamic_context_hashes:
            filtered.append(message)
            newly_injected.add(context_hash)
    return filtered, newly_injected


def build_input_messages(
    content: str | list[dict[str, Any]],
    context: InputMessageContext,
    *,
    people: list[PersonIdentity] | None = None,
    channels: list[ChannelIdentity] | None = None,
    systems: list[SystemIdentity] | None = None,
    injected_dynamic_context_hashes: set[str] | None = None,
) -> list[RunMessage]:
    injected = (
        injected_dynamic_context_hashes if injected_dynamic_context_hashes is not None else set()
    )
    messages: list[RunMessage] = []
    for identity, builder in (
        *((person, person_introduction) for person in people or []),
        *((channel, channel_introduction) for channel in channels or []),
        *((system, system_introduction) for system in systems or []),
    ):
        message = builder(identity)  # type: ignore[arg-type]
        context_hash = dynamic_context_hash(message["content"])
        if context_hash is None or context_hash in injected:
            continue
        messages.append(message)
        injected.add(context_hash)
    if context["kind"] == "human":
        messages.append(human_input(content, context))
    else:
        messages.append(system_input(content, context))
    return messages


def build_run_input(
    content: str | list[dict[str, Any]],
    context: InputMessageContext,
    *,
    people: list[PersonIdentity] | None = None,
    channels: list[ChannelIdentity] | None = None,
    systems: list[SystemIdentity] | None = None,
    injected_dynamic_context_hashes: set[str] | None = None,
    files: dict[str, Any] | None = None,
) -> RunInput:
    result: RunInput = {
        "messages": build_input_messages(
            content,
            context,
            people=people,
            channels=channels,
            systems=systems,
            injected_dynamic_context_hashes=injected_dynamic_context_hashes,
        )
    }
    if files is not None:
        result["files"] = files
    return result


def wrap_system_prompt(text: str, *, additions: list[str] | None = None) -> str:
    if text.startswith(_SYSTEM_WRAPPER_MARKER) and text.endswith("</system-instructions>"):
        if not additions:
            return text
        closing = "</system-instructions>"
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
    return "\n".join(
        [_SYSTEM_WRAPPER_MARKER, str(identity), message, *extras, "</system-instructions>"]
    )
