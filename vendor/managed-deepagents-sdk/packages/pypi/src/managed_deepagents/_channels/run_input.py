"""What a channel handler starts a run with, as ``agent.invoke`` takes it.

Normalized to the JSON the Agent Server accepts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, cast
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
    from langgraph_sdk.schema import MultitaskStrategy

    from managed_deepagents._channels.types import ChannelDestination, ChannelReply, JsonValue
    from managed_deepagents.schedules import ScheduleThreadConfig


ChannelRunInput = str | BaseMessage | Mapping[str, object]
"""A user message's text, a message, or state such as ``{"messages": [...]}``."""


class ChannelRunOptions(TypedDict, total=False):
    """Run options a handler may set with its input.

    Experimental: this may change in a minor release.
    """

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
    thread: ScheduleThreadConfig
    """The thread the run starts on, as ``define_schedule`` takes it:
    ``{"mode": "ephemeral"}`` for a fresh thread, deleted once the run ends, or
    ``{"mode": "persistent", "id": "<uuid>"}`` for the thread with that id, kept
    across events. Omitted, the run starts on the event's conversation. GitHub,
    Linear, HTTP and Slack channels only."""
    reply: ChannelReply
    """Whether a run on the event's conversation posts its final answer there,
    overriding the channel's ``reply`` for this run. Only without ``thread``."""
    multitask_strategy: MultitaskStrategy
    """What the run does while its thread already runs, overriding the
    channel's ``multitask_strategy`` for this run. GitHub, Linear, HTTP and
    Slack channels only."""
    deliver_to: ChannelRunDeliveryTarget
    """Where a run on a ``thread`` posts its final answer; omitted, nowhere.
    Only with ``thread``, and for now only to the event's own conversation:
    ``{"channel": "<this channel>", "to": {"type": "current_thread"}}``."""


class ChannelRunDeliveryTarget(TypedDict):
    """Where a run on a thread of the handler's choosing posts its final answer.

    The shape ``define_schedule``'s ``deliver_to`` takes, whose ``to`` may also
    be ``{"type": "current_thread"}``: the conversation the event came from.
    """

    channel: str
    to: ChannelDestination


ChannelRunResult = (
    ChannelRunInput
    | tuple[ChannelRunInput, ChannelRunOptions]
    | list[tuple[ChannelRunInput, ChannelRunOptions]]
)
"""What a channel handler may return to start a run: the input, the input with
run options, or, on GitHub, Linear, HTTP and Slack channels, a list of
``(input, options)`` to start several runs, at most one of them on the event's
conversation.

Experimental: this may change in a minor release.
"""


class RunThreadPersistent(TypedDict):
    mode: Literal["persistent"]
    id: str
    """A lowercase UUID."""


class RunThreadEphemeral(TypedDict):
    mode: Literal["ephemeral"]


RunThread = RunThreadEphemeral | RunThreadPersistent
"""A run's thread as ``normalize_run_inputs`` checked it."""


class NormalizedRunInput(TypedDict):
    """A handler's result as the Agent Server takes it."""

    messages: list[dict[str, JsonValue]]
    """Messages as JSON, each with a ``role``."""
    state: dict[str, JsonValue]
    """Other state keys."""
    context: NotRequired[dict[str, JsonValue]]
    metadata: NotRequired[dict[str, JsonValue]]
    webhook: NotRequired[str]
    thread: NotRequired[RunThread]
    reply: NotRequired[ChannelReply]
    multitask_strategy: NotRequired[MultitaskStrategy]
    deliver_to_conversation: NotRequired[Literal[True]]
    """The run posts its final answer on the event's conversation."""


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
    """Normalize what a handler returned, as one run on the event's conversation.

    ``None`` starts nothing; anything else that is not valid input raises
    ``InvalidRunInput``, a ``TypeError``. A run option that is ``None`` is not set.
    The options that choose a thread are refused: only ``normalize_run_inputs``
    takes them.
    """
    if value is None:
        return None
    return _normalize_result(value, None)


class RunChannel(TypedDict):
    """The channel a handler's runs start from, which ``deliver_to`` may name."""

    name: str
    provider: str


def normalize_run_inputs(value: object, channel: RunChannel) -> list[NormalizedRunInput]:
    """Normalize what a handler of a GitHub, Linear, HTTP or Slack channel returned.

    ``None`` or an empty list starts nothing. A list starts a run per
    ``(input, options)`` in it. Options may choose each run's thread, reply,
    ``multitask_strategy`` and ``deliver_to``. At most one run may start on the
    event's conversation (one without ``thread``). Anything else raises
    ``InvalidRunInput``.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return [_normalize_result(value, channel)]
    runs = [_normalize_result(item, channel, listed=True) for item in cast("list[object]", value)]
    if sum("thread" not in run for run in runs) > 1:
        raise InvalidRunInput("at most one run may start on the event's conversation")
    return runs


def starts_on_conversation(result: object) -> bool:
    """Whether a handler's result starts a run on the event's conversation:
    anything but nothing, or a list whose every run has a ``thread``."""
    if result is None:
        return False
    if not isinstance(result, list):
        return True
    return any(
        not (
            isinstance(item, tuple)
            and len(item) > 1
            and isinstance(item[1], Mapping)
            and item[1].get("thread") is not None
        )
        for item in cast("list[object]", result)
    )


def _normalize_result(
    value: object, channel: RunChannel | None, *, listed: bool = False
) -> NormalizedRunInput:
    if isinstance(value, tuple):
        items = cast("tuple[object, ...]", value)
        if len(items) not in (1, 2):
            raise InvalidRunInput("run input must be (input, options)")
        normalized = _normalize_input(items[0])
        return normalized if len(items) == 1 else _with_options(normalized, items[1], channel)
    if listed:
        raise InvalidRunInput("each run in a list must be (input, options)")
    return _normalize_input(value)


_RUN_OPTIONS = frozenset({"context", "metadata", "webhook"})
# The options that choose where a run starts and answers; GitHub, Linear, HTTP and
# Slack channels only.
_ROUTING_OPTIONS = frozenset({"thread", "reply", "multitask_strategy", "deliver_to"})
_MULTITASK_STRATEGIES = frozenset({"enqueue", "interrupt", "rollback", "reject"})
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
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


def _with_options(
    normalized: NormalizedRunInput, options: object, channel: RunChannel | None
) -> NormalizedRunInput:
    if not isinstance(options, Mapping):
        raise InvalidRunInput("run options must be a mapping")
    allowed = _RUN_OPTIONS if channel is None else _RUN_OPTIONS | _ROUTING_OPTIONS
    extra = sorted(str(key) for key in options if key not in allowed)
    if extra:
        raise InvalidRunInput(
            f"unsupported run option {_named(', '.join(extra))}; use "
            + ("context, metadata or webhook" if channel is None else ", ".join(sorted(allowed)))
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
    if channel is not None:
        _with_routing(result, cast("Mapping[str, object]", options), channel)
    return result


def _with_routing(
    result: NormalizedRunInput, options: Mapping[str, object], channel: RunChannel
) -> None:
    """Check the options that choose a run's thread and where it answers.

    ``reply`` belongs to a run on the event's conversation, and ``deliver_to`` to
    a run on a thread of the handler's choosing.
    """
    thread = options.get("thread")
    if thread is not None:
        result["thread"] = _run_thread(thread)
    reply = options.get("reply")
    if reply is not None:
        if thread is not None:
            raise InvalidRunInput("reply is only for a run on the event's conversation")
        if reply not in ("auto", "none"):
            raise InvalidRunInput('run reply must be "auto" or "none"')
        result["reply"] = cast("ChannelReply", reply)
    strategy = options.get("multitask_strategy")
    if strategy is not None:
        if strategy not in _MULTITASK_STRATEGIES:
            raise InvalidRunInput(
                "run multitask_strategy must be enqueue, interrupt, rollback or reject"
            )
        result["multitask_strategy"] = cast("MultitaskStrategy", strategy)
    deliver_to = options.get("deliver_to")
    if deliver_to is not None:
        if thread is None:
            raise InvalidRunInput("deliver_to is only for a run with a thread")
        _check_deliver_to(deliver_to, channel)
        result["deliver_to_conversation"] = True


def _run_thread(thread: object) -> RunThread:
    fields = cast("Mapping[str, object]", thread if isinstance(thread, Mapping) else {"mode": None})
    # Ephemeral by default, as a schedule's thread is.
    mode = fields.get("mode", "ephemeral")
    if mode == "ephemeral" and set(fields) <= {"mode"}:
        return {"mode": "ephemeral"}
    thread_id = fields.get("id")
    if (
        mode == "persistent"
        and set(fields) == {"mode", "id"}
        and isinstance(thread_id, str)
        and _UUID.fullmatch(thread_id)
    ):
        return {"mode": "persistent", "id": thread_id.lower()}
    raise InvalidRunInput(
        'run thread must be {"mode": "ephemeral"} or {"mode": "persistent", "id": "<uuid>"}'
    )


def _check_deliver_to(deliver_to: object, channel: RunChannel) -> None:
    """Only the event's own conversation, for now."""
    to = deliver_to.get("to") if isinstance(deliver_to, Mapping) else None
    if (
        not isinstance(deliver_to, Mapping)
        or set(deliver_to) != {"channel", "to"}
        or deliver_to.get("channel") not in (channel["name"], channel["provider"])
        or not isinstance(to, Mapping)
        or dict(to) != {"type": "current_thread"}
    ):
        raise InvalidRunInput(
            'run deliver_to must be {"channel": "<this channel>", "to": {"type": "current_thread"}}'
        )


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
    return dict(context)


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
    return dict(metadata)


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
