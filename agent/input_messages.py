"""Typed construction and serialization for application-owned model inputs."""

import hashlib
from collections.abc import Mapping
from html import escape
from typing import Any, Literal, NotRequired, TypedDict
from xml.etree import ElementTree

from langchain_core.messages import BaseMessage

INJECTED_DYNAMIC_CONTEXT_HASHES_KEY = "injected_dynamic_context_hashes"
# Written by the deepagents summarization middleware; the prompt it builds is the
# summary message followed by messages[cutoff_index:].
SUMMARIZATION_EVENT_KEY = "_summarization_event"

Surface = Literal["slack", "linear", "github", "web", "desktop", "automation", "eval"]
EntityKind = Literal["person", "channel", "system"]
MessageKind = Literal["human", "system"]


class PersonIdentity(TypedDict):
    """Everything the agent knows about one person, independent of any surface.

    The surface a message arrived on belongs to its envelope, so this block is
    identical whichever way the person reached the thread and is re-sent only
    when their own data changes.
    """

    id: str
    display_name: NotRequired[str]
    github_login: NotRequired[str]
    commit_name: NotRequired[str]
    commit_email: NotRequired[str]
    email: NotRequired[str]
    timezone: NotRequired[str]
    open_swe_account: NotRequired[Literal["linked", "unlinked"]]
    workspace_admin: NotRequired[Literal["yes", "no"]]
    new_prs: NotRequired[Literal["as drafts", "ready for review"]]
    standing_instructions: NotRequired[str]


class ChannelIdentity(TypedDict):
    """The conversation a message arrived in, with whatever stays true of it.

    Thread-constant data belongs here rather than in a per-turn message: the
    block is deduped by content, so the model is told once and told again only
    when something about the conversation actually changes.
    """

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
    id: NotRequired[str]


class RunInput(TypedDict):
    messages: list[RunMessage]
    files: NotRequired[dict[str, Any]]


_ENTITY_FIELDS: dict[EntityKind, tuple[str, ...]] = {
    "person": (
        "display_name",
        "github_login",
        "commit_name",
        "commit_email",
        "email",
        "timezone",
        "open_swe_account",
        "workspace_admin",
        "new_prs",
        "standing_instructions",
    ),
    "channel": (
        "platform",
        "name",
        "thread_id",
        "topic",
        "purpose",
        "description",
        "default_repo",
        "web_url",
        "trace_url",
    ),
    "system": ("display_name", "platform", "sender_type", "content"),
}
_UNTRUSTED_ENTITY_FIELDS = frozenset({"topic", "purpose", "description"})


def _xml_text(value: object) -> str:
    return escape(str(value), quote=False)


def _xml_attr(value: object) -> str:
    return escape(str(value), quote=True)


def split_person_id(person: PersonIdentity) -> tuple[str, str]:
    """``(platform, external id)`` from ``person["id"]``; platform may be empty."""
    platform, separator, external_id = person["id"].partition(":")
    if not separator:
        return "", person["id"]
    return platform, external_id


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


def message_sender_id(content: object, *, kind: MessageKind | None = None) -> str | None:
    for message in _input_message_elements(content):
        sender = message.get("sender")
        if sender and (kind is None or message.get("kind") == kind):
            return sender
    return None


def input_message_text(content: object) -> str | None:
    """The authored text carried by a serialized input message, when present."""
    texts = [
        body.strip()
        for message in _input_message_elements(content)
        if (body := message.findtext("content")) and body.strip()
    ]
    return "\n\n".join(texts) or None


def input_message_timestamps(content: object) -> set[str]:
    """Source-message timestamps the serialized envelopes in ``content`` carry."""
    return {
        timestamp
        for message in _input_message_elements(content)
        if (timestamp := message.get("timestamp"))
    }


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


def _message_content(message: object) -> object:
    """The content of a message, whether it is a model object or its JSON form."""
    if isinstance(message, BaseMessage):
        return message.content
    if isinstance(message, Mapping):
        return message.get("content")
    return None


def dynamic_context_hashes_from_messages(messages: object) -> set[str]:
    if not isinstance(messages, (list, tuple)):
        return set()
    hashes: set[str] = set()
    for message in messages:
        context_hash = dynamic_context_hash(_message_content(message))
        if context_hash is not None:
            hashes.add(context_hash)
    return hashes


def visible_dynamic_context_hashes(state: Mapping[str, Any]) -> set[str]:
    """Context hashes the model can still see, so the rest is reintroduced.

    Summarization replaces everything before ``cutoff_index`` with a summary, so a
    context block behind the cutoff is gone from the prompt while still sitting in
    state. Treating it as introduced is what leaves the model unable to resolve a
    sender it is still being shown messages from.
    """
    messages = state.get("messages")
    if not isinstance(messages, (list, tuple)):
        return set()
    event = state.get(SUMMARIZATION_EVENT_KEY)
    cutoff = event.get("cutoff_index") if isinstance(event, Mapping) else None
    if isinstance(cutoff, int) and cutoff >= 0:
        messages = messages[cutoff:]
    return dynamic_context_hashes_from_messages(messages)


def _entity_field_line(field: str, value: object) -> str:
    label = f"{field} (untrusted)" if field in _UNTRUSTED_ENTITY_FIELDS else field
    text = _xml_text(value)
    if "\n" not in text:
        return f"{label}: {text}"
    indented = "\n".join(f"  {line}" for line in text.split("\n"))
    return f"{label}:\n{indented}"


def _entity_message(identity: Identity, kind: EntityKind) -> RunMessage:
    entity_id = _validate_entity_id(identity["id"])
    lines: list[str] = []
    for field in _ENTITY_FIELDS[kind]:
        value = identity.get(field)  # type: ignore[union-attr]
        if value is None or value == "":
            continue
        lines.append(_entity_field_line(field, value))
    body = "\n".join(lines)
    canonical = f'<dynamic-context kind="{kind}" id="{_xml_attr(entity_id)}">'
    if body:
        canonical += f"\n{body}\n"
    canonical += "</dynamic-context>"
    return {"role": "user", "content": canonical}


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
        f'sender="{_xml_attr(sender_id)}"',
        f'surface="{context["surface"]}"',
        f'kind="{context["kind"]}"',
    ]
    channel_id = context.get("channel_id")
    if channel_id:
        attributes.insert(1, f'channel="{_xml_attr(_validate_entity_id(channel_id))}"')
    children: list[str] = []
    for name, value in context.get("data", {}).items():
        if isinstance(value, (dict, list, tuple)):
            children.append(_data_element(name, value))
        elif not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"invalid structured data field: {name}")
        else:
            attributes.append(f'{name}="{_xml_attr(value)}"')
    children.append(f"<content>{_xml_text(text)}</content>")
    body = "\n".join(children)
    return f"<input-message {' '.join(attributes)}>\n{body}\n</input-message>"


_ENVELOPE_CLOSE = "</input-message>"


def _splice_envelope_data(text: str, element: str) -> str | None:
    if "<input-message " not in text:
        return None
    close = text.rfind(_ENVELOPE_CLOSE)
    if close == -1:
        return None
    return f"{text[:close]}{element}\n{text[close:]}"


def append_message_data(
    content: str | list[Any], name: str, value: object
) -> str | list[Any] | None:
    """Add a data field to an already-serialized envelope, or None when there is none."""
    element = _data_element(name, value)
    if isinstance(content, str):
        return _splice_envelope_data(content, element)
    for index in range(len(content) - 1, -1, -1):
        block = content[index]
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        if not isinstance(block.get("text"), str):
            continue
        spliced = _splice_envelope_data(block["text"], element)
        if spliced is None:
            continue
        return [*content[:index], {**block, "text": spliced}, *content[index + 1 :]]
    return None


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
    channels: list[ChannelIdentity] | None = None,
    systems: list[SystemIdentity] | None = None,
    injected_dynamic_context_hashes: set[str] | None = None,
) -> list[RunMessage]:
    injected = (
        injected_dynamic_context_hashes if injected_dynamic_context_hashes is not None else set()
    )
    messages: list[RunMessage] = []
    introductions = [
        *(channel_introduction(channel) for channel in channels or []),
        *(system_introduction(system) for system in systems or []),
    ]
    for message in introductions:
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
    channels: list[ChannelIdentity] | None = None,
    systems: list[SystemIdentity] | None = None,
    injected_dynamic_context_hashes: set[str] | None = None,
    files: dict[str, Any] | None = None,
) -> RunInput:
    result: RunInput = {
        "messages": build_input_messages(
            content,
            context,
            channels=channels,
            systems=systems,
            injected_dynamic_context_hashes=injected_dynamic_context_hashes,
        )
    }
    if files is not None:
        result["files"] = files
    return result
