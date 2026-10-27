"""Current-delivery facts and the optional bound post operation."""

from __future__ import annotations

import inspect
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, TypeGuard, cast, overload

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter

from managed_deepagents._channels.types import (
    ChannelEvent,
    ChannelInfo,
    ChannelMessage,
    JsonValue,
    PostedChannelMessage,
    PostedChannelThread,
)

if TYPE_CHECKING:
    from managed_deepagents._channels.run_input import ChannelRunResult


class ChannelMessagingAdapter(Protocol):
    def post(
        self, *, message: ChannelMessage, target: JsonValue, new_thread: bool = False
    ) -> PostedChannelMessage | Awaitable[PostedChannelMessage]: ...


def immutable_copy(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: immutable_copy(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(immutable_copy(item) for item in value)
    return value


def json_copy(value: JsonValue) -> JsonValue:
    """Copy validated JSON into ordinary objects and arrays for provider clients."""
    if isinstance(value, Mapping):
        return {key: json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_copy(item) for item in value]
    return value


def channel_info(
    *, name: str, provider: str, event: Mapping[str, object] | None, raw_event: JsonValue = None
) -> ChannelInfo:
    return ChannelInfo(
        name,
        provider,
        cast("ChannelEvent | None", immutable_copy(event)),
        cast("JsonValue", immutable_copy(raw_event)),
    )


class ChannelPost(Protocol):
    @overload
    def __call__(
        self, message: ChannelMessage, *, new_thread: Literal[False] = False
    ) -> Awaitable[PostedChannelMessage]: ...

    @overload
    def __call__(
        self,
        message: ChannelMessage,
        *,
        new_thread: Literal[True],
        run: ChannelRunResult | None = None,
    ) -> Awaitable[PostedChannelThread]: ...

    def __call__(
        self,
        message: ChannelMessage,
        *,
        new_thread: bool = False,
        run: ChannelRunResult | None = None,
    ) -> Awaitable[PostedChannelMessage]:
        """Post in this thread, or with ``new_thread=True`` at top level.

        A top-level post starts a new provider thread in the same conversation,
        which replies continue, also without a mention in Slack's thread mode.
        It returns the thread's ``thread_id``. ``run`` starts a run in it with a
        channel handler's run input. Only Trigger channels that name the new
        thread support it.
        """
        ...


StartThread = Callable[[ChannelMessage, "ChannelRunResult | None"], Awaitable[PostedChannelThread]]


@dataclass(frozen=True)
class RuntimeChannel(ChannelInfo):
    post: ChannelPost | None = None


def create_runtime_channel(
    *,
    name: str,
    provider: str,
    event: Mapping[str, object] | None,
    target: JsonValue,
    transport: ChannelMessagingAdapter | None = None,
    raw_event: JsonValue = None,
    start_thread: StartThread | None = None,
) -> RuntimeChannel:
    facts = channel_info(name=name, provider=provider, event=event, raw_event=raw_event)
    # Keep the route private and copy it so authored code cannot change the target.

    if transport is None:
        return RuntimeChannel(facts.name, facts.provider, facts.event, facts.raw_event)
    bound_target = json_copy(target)
    # Only a schedule event chooses a new provider thread.
    schedule = (
        facts.event if facts.event is not None and facts.event["type"] == "schedule" else None
    )

    async def post(
        message: ChannelMessage,
        *,
        new_thread: bool = False,
        run: ChannelRunResult | None = None,
    ) -> PostedChannelMessage:
        validate_message(message)
        if new_thread:
            if start_thread is None:
                raise ValueError("this channel cannot start a new thread")
            return await start_thread(
                cast("ChannelMessage", json_copy(cast("JsonValue", message))), run
            )
        if run is not None:
            raise ValueError("a post starts a run only with new_thread=True")
        result = transport.post(
            message=cast("ChannelMessage", json_copy(cast("JsonValue", message))),
            target=json_copy(bound_target),
            **({"new_thread": bool(schedule["new_thread"])} if schedule is not None else {}),
        )
        return cast("PostedChannelMessage", await result if inspect.isawaitable(result) else result)

    return RuntimeChannel(
        facts.name, facts.provider, facts.event, facts.raw_event, cast("ChannelPost", post)
    )


def _json_value(value: object) -> object:
    if not is_json_value(value):
        raise ValueError("must be finite acyclic JSON")
    return value


def _message_content(value: object) -> object:
    if not isinstance(value, str) and not (
        isinstance(value, (list, tuple))
        and all(
            isinstance(block, Mapping)
            and isinstance(block.get("type"), str)
            and is_json_value(block)
            for block in value
        )
    ):
        raise ValueError("content must be a string or JSON content blocks")
    return value


class _ContentMessageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["content"]
    content: Annotated[object, AfterValidator(_message_content)]


class _NativeMessageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["native"]
    native: Annotated[object, AfterValidator(_json_value)]


_MESSAGE_ADAPTER = TypeAdapter(
    Annotated[_ContentMessageModel | _NativeMessageModel, Field(discriminator="type")]
)


def validate_message(message: Mapping[str, object]) -> None:
    # Validate only; keep provider-owned values intact for the adapter.
    _MESSAGE_ADAPTER.validate_python(dict(message) if isinstance(message, Mapping) else message)


def require_text_content(message: Mapping[str, object]) -> str:
    validate_message(message)
    if message["type"] == "native":
        raise ValueError(
            "text-only channel adapter requires content; native messages are not supported"
        )
    content = message["content"]
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)) or not all(
        isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        for block in content
    ):
        raise ValueError("channel transport supports text content only")
    return "".join(block["text"] for block in content)


def is_json_value(value: object, ancestors: set[int] | None = None) -> TypeGuard[JsonValue]:
    """Accept finite JSON values, including frozen mappings and tuples, without cycles."""
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if type(value) not in (dict, MappingProxyType, list, tuple):
        return False
    ancestors = set() if ancestors is None else ancestors
    identity = id(value)
    if identity in ancestors:
        return False
    ancestors.add(identity)
    if isinstance(value, Mapping):
        valid = all(
            isinstance(key, str) and is_json_value(item, ancestors) for key, item in value.items()
        )
    else:
        valid = all(
            is_json_value(item, ancestors)
            for item in cast("list[object] | tuple[object, ...]", value)
        )
    ancestors.remove(identity)
    return valid
