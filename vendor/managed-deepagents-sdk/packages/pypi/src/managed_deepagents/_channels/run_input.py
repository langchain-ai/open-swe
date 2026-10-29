"""What a channel handler starts a run with, as ``agent.invoke`` takes it.

Normalized to the JSON the Agent Server accepts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, NotRequired, TypedDict, cast
from urllib.parse import urlsplit

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    MessageLikeRepresentation,
    convert_to_messages,
)

from managed_deepagents._channels.runtime import is_json_value
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from managed_deepagents._channels.types import JsonValue


ChannelRunInput = str | BaseMessage | Mapping[str, object]
"""A user message's text, a message, or state such as ``{"messages": [...]}``."""


class ChannelRunOptions(TypedDict, total=False):
    """Run options a handler may set with its input."""

    context: Mapping[str, object]
    """Run context, as ``agent.invoke(input, context=...)`` takes it."""
    metadata: Mapping[str, JsonValue]
    """Metadata for the run, such as where it came from, to find it by in
    LangSmith. Keys are letters, digits, ``_``, ``.`` or ``-`` and start with a
    letter. Keys the Agent Server and LangSmith set are reserved: those starting
    with ``mda_``, ``langgraph_`` or ``ls_``, and ids such as ``thread_id``,
    ``session_id``, ``run_id`` or ``user_id``."""
    webhook: str
    """A URL the Agent Server POSTs the finished run to (its status, metadata,
    values and any error), as LangGraph's ``runs.create(webhook=...)`` does: from
    the server, so it reaches you even if the deployment restarts. http(s),
    without credentials in it."""


ChannelRunResult = ChannelRunInput | tuple[ChannelRunInput, ChannelRunOptions]
"""What a channel handler may return to start a run: the input, or the input
with run options."""


class NormalizedRunInput(TypedDict):
    """A handler's result as the Agent Server takes it."""

    messages: list[dict[str, JsonValue]]
    """Messages as JSON, each with a ``role``."""
    state: dict[str, JsonValue]
    """Other state keys."""
    context: NotRequired[dict[str, JsonValue]]
    metadata: NotRequired[dict[str, JsonValue]]
    webhook: NotRequired[str]


class RunStartOptions(TypedDict):
    """A handler's run input as ``start_ingress_run`` takes it."""

    messages: list[dict[str, JsonValue]]
    state: NotRequired[dict[str, JsonValue]]
    metadata: NotRequired[dict[str, JsonValue]]
    webhook: NotRequired[str]


def run_start_options(
    run_input: NormalizedRunInput, metadata: Mapping[str, JsonValue] | None = None
) -> RunStartOptions:
    """A handler's run input as ``start_ingress_run`` takes it.

    MDA's own ``metadata`` goes over the handler's, so a handler can't replace
    it. Empty state and metadata are left out.
    """
    options: RunStartOptions = {"messages": run_input["messages"]}
    if run_input["state"]:
        options["state"] = run_input["state"]
    merged = {**run_input.get("metadata", {}), **(metadata or {})}
    if merged:
        options["metadata"] = merged
    if "webhook" in run_input:
        options["webhook"] = run_input["webhook"]
    return options


_MESSAGE_ROLES = frozenset({"human", "ai", "system", "tool"})


class InvalidRunInput(TypeError):  # noqa: N818 - named for what it is, as TypeScript's
    """Run input MDA starts no run with.

    Its ``reason``, which is also its message, says what is wrong in a fixed
    phrase. A key or type it names is bounded to one log line, so the channel
    logs the reason, where it logs no other exception's message. A
    ``TypeError``, as invalid run input raised before.
    """

    reason: str
    """Why MDA refused the input, such as ``run metadata key is reserved: user_id``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _named(value: object) -> str:
    """A key or type the handler chose, as one bounded log line holds it."""
    return logger.log_identifier(str(value))


def normalize_run_input(value: object) -> NormalizedRunInput | None:
    """Normalize what a handler returned.

    ``None`` starts nothing; anything else that is not valid input raises
    ``InvalidRunInput``, a ``TypeError``. A run option that is ``None`` is not set.
    """
    if value is None:
        return None
    if isinstance(value, tuple):
        items = cast("tuple[object, ...]", value)
        if len(items) not in (1, 2):
            raise InvalidRunInput("run input must be (input, options)")
        normalized = _normalize_input(items[0])
        return normalized if len(items) == 1 else _with_options(normalized, items[1])
    return _normalize_input(value)


_RUN_OPTIONS = frozenset({"context", "metadata", "webhook"})
# The metadata keys a handler may set.
_METADATA_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
# Keys MDA, the Agent Server and LangSmith set, which a handler's would override
# or confuse (LangSmith groups a thread's runs by thread_id, session_id or
# conversation_id), and object internals.
_RESERVED_METADATA_KEY = re.compile(
    r"(mda_|langgraph_|ls_).*|assistant_id|graph_id|run_id|run_attempt|thread_id|session_id"
    r"|conversation_id|user_id|owner|checkpoint_id|checkpoint_ns|constructor|prototype",
    re.IGNORECASE,
)


def _with_options(normalized: NormalizedRunInput, options: object) -> NormalizedRunInput:
    if not isinstance(options, Mapping):
        raise InvalidRunInput("run options must be a mapping")
    extra = sorted(str(key) for key in options if key not in _RUN_OPTIONS)
    if extra:
        raise InvalidRunInput(
            f"unsupported run option {_named(', '.join(extra))}; use context, metadata or webhook"
        )
    result: NormalizedRunInput = {**normalized}
    context = options.get("context")
    if context is not None:
        result["context"] = _run_context(context)
    metadata = options.get("metadata")
    if metadata is not None:
        result["metadata"] = _run_metadata(metadata)
    webhook = options.get("webhook")
    if webhook is not None:
        result["webhook"] = _run_webhook(webhook)
    return result


_WEBHOOK_PREFIX = re.compile(r"https?://", re.IGNORECASE)
_UNSAFE_URL_CHARACTER = re.compile(r"[\s\x00-\x1f\x7f]")


def _run_webhook(webhook: object) -> str:
    """The webhook as given, once it is ``http://`` or ``https://``, a host and an
    optional port, without credentials, whitespace or control characters. The
    TypeScript runtime checks it the same way, so both pass the Agent Server the
    same string."""
    error = InvalidRunInput("run webhook must be an http(s) URL, without credentials in it")
    if (
        not isinstance(webhook, str)
        or not _WEBHOOK_PREFIX.match(webhook)
        or _UNSAFE_URL_CHARACTER.search(webhook)
    ):
        raise error
    try:
        parts = urlsplit(webhook)
        _ = parts.port  # urlsplit checks the port only when it is read.
    except ValueError:
        raise error from None
    if not parts.hostname or parts.username or parts.password:
        raise error
    return webhook


def _run_context(context: object) -> dict[str, JsonValue]:
    if not isinstance(context, Mapping) or not is_json_value(dict(context)):
        raise InvalidRunInput("run context must be a JSON object")
    for key in ("channel", "mda_channel"):
        if key in context:
            raise InvalidRunInput(f"context.{key} is reserved for managed ingress")
    return cast("dict[str, JsonValue]", dict(context))


def _run_metadata(metadata: object) -> dict[str, JsonValue]:
    if not isinstance(metadata, Mapping) or not is_json_value(dict(metadata)):
        raise InvalidRunInput("run metadata must be a JSON object")
    # A JSON object's keys are strings.
    for key in cast("Mapping[str, object]", metadata):
        if not _METADATA_KEY.fullmatch(key):
            raise InvalidRunInput(
                "run metadata key is not up to 64 letters, digits, _, . or -, "
                f"starting with a letter: {_named(key)}"
            )
        if _RESERVED_METADATA_KEY.fullmatch(key):
            raise InvalidRunInput(f"run metadata key is reserved: {_named(key)}")
    return cast("dict[str, JsonValue]", dict(metadata))


def _normalize_input(value: object) -> NormalizedRunInput:
    if isinstance(value, str):
        if not value.strip():
            raise InvalidRunInput("run input must not be blank")
        return {"messages": [_message_json(HumanMessage(value))], "state": {}}
    if isinstance(value, BaseMessage):
        return {"messages": [_message_json(value)], "state": {}}
    if not isinstance(value, Mapping):
        raise InvalidRunInput("run input must be a string, a message, or a mapping of state")
    state = {str(key): item for key, item in value.items() if key != "messages"}
    messages = value.get("messages")
    if messages is not None and not isinstance(messages, list | tuple):
        raise InvalidRunInput("run input messages must be a list")
    if not is_json_value(state):
        raise InvalidRunInput("run input state must be JSON")
    normalized = [_message_like(message) for message in messages or ()]
    if not normalized and not state:
        raise InvalidRunInput("run input must not be empty")
    return {"messages": normalized, "state": cast("dict[str, JsonValue]", state)}


def _message_like(message: object) -> dict[str, JsonValue]:
    if isinstance(message, str) and not message.strip():
        raise InvalidRunInput("a message must not be blank")
    try:
        [coerced] = convert_to_messages([cast("MessageLikeRepresentation", message)])
    except (NotImplementedError, ValueError) as error:
        raise InvalidRunInput("a message must be a string, a message, or JSON") from error
    return _message_json(coerced)


def _message_json(message: BaseMessage) -> dict[str, JsonValue]:
    """A message as the JSON the Agent Server turns back into a message.

    Its ``model_dump()`` fields, with a ``role`` like the channel's other messages.
    """
    role = message.type
    if role not in _MESSAGE_ROLES:
        raise InvalidRunInput(f"unsupported message type {_named(role)}")
    json = {**message.model_dump(mode="json", exclude={"type"}, exclude_none=True), "role": role}
    if not is_json_value(json):
        raise InvalidRunInput("a message must be JSON")
    return cast("dict[str, JsonValue]", json)
