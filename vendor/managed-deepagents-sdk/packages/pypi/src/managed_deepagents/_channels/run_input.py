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
from managed_deepagents._channels.target import is_channel_target, names_origin
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from langgraph_sdk.schema import MultitaskStrategy

    from managed_deepagents._channels.types import ChannelReply, ChannelTarget, JsonValue
    from managed_deepagents.schedules import ThreadConfig


ChannelRunInput = str | BaseMessage | Mapping[str, object]
"""A user message's text, a message, or state such as ``{"messages": [...]}``."""


class ChannelRun(TypedDict):
    """A run a handler starts with options: its input, and where and how it runs.

    Experimental: this may change in a minor release.
    """

    input: ChannelRunInput
    """The run's input: a user message's text, a message, or state such as
    ``{"messages": [...]}``."""
    thread: NotRequired[ThreadConfig]
    """The thread the run starts on: ``{"mode": "ephemeral"}`` for a fresh
    thread, deleted once the run ends, or ``{"mode": "persistent", "id":
    "<uuid>"}`` for the thread with that id, kept across events. Omitted, the
    thread MDA gives the handler, the event's conversation, as is a persistent
    thread with its id. GitHub, Linear, HTTP and Slack channels only."""
    auto_post: NotRequired[bool | ChannelTarget]
    """Where the run posts its final answer. ``True`` is the event's
    conversation and ``False`` nowhere. A ``ChannelTarget`` names a conversation;
    for now only the event's own, such as a ``GitHubChannelTarget`` for its
    issue or a run's ``channel.target`` passed back, which is ``True``.
    Omitted, the conversation's run posts as the channel's ``auto_post`` says,
    and a run on any other thread posts nothing. GitHub, Linear, HTTP and
    Slack channels only."""
    multitask_strategy: NotRequired[MultitaskStrategy]
    """What the run does while its thread already runs, overriding the
    channel's ``multitask_strategy`` for this run. GitHub, Linear, HTTP and
    Slack channels only."""
    context: NotRequired[Mapping[str, object]]
    """Run context, as ``agent.invoke(input, context=...)`` takes it."""
    metadata: NotRequired[Mapping[str, JsonValue]]
    """Metadata for the run, such as where it came from, to find it by in
    LangSmith. Keys are letters, digits, ``_``, ``.`` or ``-`` and start with a
    letter. Keys the Agent Server and LangSmith set are reserved: those starting
    with ``mda_``, ``langgraph_`` or ``ls_``, and ids such as ``thread_id``,
    ``session_id``, ``run_id`` or ``user_id``."""
    webhook: NotRequired[str]
    """A URL the Agent Server POSTs the finished run to (its status, metadata,
    values and any error), as LangGraph's ``runs.create(webhook=...)`` does: from
    the server, so it reaches you even if the deployment restarts. http(s),
    without credentials in it."""


ChannelRunResult = ChannelRunInput | list[ChannelRun]
"""What a channel handler may return to start a run: its input, as one run on
the event's conversation, or a list of ``ChannelRun`` to set run options. On
GitHub, Linear, HTTP and Slack channels a list may start several runs, at most
one of them on the event's conversation; elsewhere it holds one run.

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

    Its input alone, or a list of one run with only ``context``, ``metadata``
    or ``webhook``. ``None`` or an empty list starts nothing; anything else that
    is not valid input raises ``InvalidRunInput``, a ``TypeError``. A run option
    that is ``None`` is not set. The options that choose a thread are refused:
    only ``normalize_run_inputs`` takes them.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        return _plain_run(value)
    runs = [_run_object(item, None) for item in cast("list[object]", value)]
    if len(runs) > 1:
        raise InvalidRunInput("this channel starts at most one run")
    return runs[0] if runs else None


class RunChannel(TypedDict):
    """The channel a handler's runs start from, which a run's ``auto_post`` names."""

    name: str
    provider: str
    conversation_thread_id: NotRequired[str | None]
    """The thread of the event's conversation. A run on a persistent thread with
    this id is the conversation's run. Without it, only a run without
    ``thread`` is."""
    origin: NotRequired[ChannelTarget | None]
    """The conversation the event came from, which a run's ``auto_post`` may
    name. Without it, a run's target is refused."""


def normalize_run_inputs(value: object, channel: RunChannel) -> list[NormalizedRunInput]:
    """Normalize what a handler of a GitHub, Linear, HTTP or Slack channel returned.

    ``None`` or an empty list starts nothing. Its input alone starts a run on
    the event's conversation; a list starts a run per run object in it, each
    of which may choose its thread, ``auto_post`` and ``multitask_strategy``.
    At most one run may start on the event's conversation: one without
    ``thread``, or with the conversation's own. Anything else raises
    ``InvalidRunInput``.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        return [_plain_run(value)]
    runs = [_run_object(item, channel) for item in cast("list[object]", value)]
    if sum("thread" not in run for run in runs) > 1:
        raise InvalidRunInput("at most one run may start on the event's conversation")
    return runs


def starts_on_conversation(result: object, conversation_thread_id: str | None = None) -> bool:
    """Whether a handler's result starts a run on the event's conversation: its
    input alone, or a run in a list without ``thread`` or with the
    conversation's own. A tuple, which is refused, starts nothing."""
    if result is None or isinstance(result, tuple):
        return False
    if not isinstance(result, list):
        return True
    return any(
        _starts_on_conversation(item, conversation_thread_id)
        for item in cast("list[object]", result)
    )


def _starts_on_conversation(item: object, conversation_thread_id: str | None) -> bool:
    if not _is_run(item):
        return False
    thread = cast("Mapping[str, object]", item).get("thread")
    if thread is None:
        return True
    thread_id = thread.get("id") if isinstance(thread, Mapping) else None
    return (
        isinstance(thread, Mapping)
        and thread.get("mode") == "persistent"
        and isinstance(thread_id, str)
        and conversation_thread_id is not None
        and thread_id.lower() == conversation_thread_id.lower()
    )


_LIST_ITEM = 'each run in a list must be an object with input, such as { input: "..." }'


def _is_run(value: object) -> bool:
    """A run in a list: a mapping with ``input``."""
    return isinstance(value, Mapping) and value.get("input") is not None


def _plain_run(value: object) -> NormalizedRunInput:
    """A handler's input alone: text, a message, or state. A tuple, the shape
    handlers returned run options in before, is refused."""
    if isinstance(value, tuple):
        raise InvalidRunInput(_LIST_ITEM)
    return _normalize_input(value)


_RUN_OPTIONS = frozenset({"context", "metadata", "webhook"})
# The options that choose where a run starts and answers; GitHub, Linear, HTTP
# and Slack channels only.
_ROUTING_OPTIONS = frozenset({"thread", "auto_post", "multitask_strategy"})
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


def _run_object(value: object, channel: RunChannel | None) -> NormalizedRunInput:
    """A run in a list: its input with its options."""
    if not _is_run(value):
        raise InvalidRunInput(_LIST_ITEM)
    run = cast("Mapping[str, object]", value)
    allowed = _RUN_OPTIONS if channel is None else _RUN_OPTIONS | _ROUTING_OPTIONS
    extra = sorted(str(key) for key in run if key != "input" and key not in allowed)
    if extra:
        raise InvalidRunInput(
            f"unsupported run option {_named(', '.join(extra))}; use "
            + ("context, metadata or webhook" if channel is None else ", ".join(sorted(allowed)))
        )
    result: NormalizedRunInput = {**_normalize_input(run["input"])}
    context = run.get("context")
    if context is not None:
        result["context"] = _run_context(context)
    metadata = run.get("metadata")
    if metadata is not None:
        result["metadata"] = _run_metadata(metadata)
    webhook = run.get("webhook")
    if webhook is not None:
        result["webhook"] = _run_webhook(webhook)
    if channel is not None:
        _with_routing(result, run, channel)
    return result


def _with_routing(
    result: NormalizedRunInput, run: Mapping[str, object], channel: RunChannel
) -> None:
    """Check the options that choose a run's thread and where it answers.

    A persistent thread with the conversation's id is the conversation, so the
    run has no ``thread``. ``auto_post`` overrides the channel's on the
    conversation; elsewhere ``True`` posts the answer on the conversation.
    """
    thread = run.get("thread")
    if thread is not None:
        run_thread = _run_thread(thread)
        conversation = channel.get("conversation_thread_id")
        if not (
            run_thread["mode"] == "persistent"
            and conversation is not None
            and run_thread["id"] == conversation.lower()
        ):
            result["thread"] = run_thread
    auto_post = run.get("auto_post")
    if auto_post is not None:
        posts = _posts_on_conversation(auto_post, channel)
        if "thread" not in result:
            result["reply"] = "auto" if posts else "none"
        elif posts:
            result["deliver_to_conversation"] = True
    strategy = run.get("multitask_strategy")
    if strategy is not None:
        if strategy not in _MULTITASK_STRATEGIES:
            raise InvalidRunInput(
                "run multitask_strategy must be enqueue, interrupt, rollback or reject"
            )
        result["multitask_strategy"] = cast("MultitaskStrategy", strategy)


_BAD_AUTO_POST = "run autoPost must be true, false, or a target on this channel for now"


def _posts_on_conversation(auto_post: object, channel: RunChannel) -> bool:
    """Whether a run's ``auto_post`` is the event's conversation; ``False`` is nowhere.

    A target counts only as the conversation the event came from: GitHub, Linear
    and HTTP channels post nowhere else yet.
    """
    if isinstance(auto_post, bool):
        return auto_post
    origin = channel.get("origin")
    if (
        not is_channel_target(auto_post)
        or origin is None
        or auto_post.provider != channel["provider"]
        or auto_post.channel not in (None, channel["name"], channel["provider"])
        or not names_origin(auto_post, origin)
    ):
        raise InvalidRunInput(_BAD_AUTO_POST)
    return True


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
