from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

    from managed_deepagents._channels.types import JsonValue, MessageContent


@dataclass(frozen=True)
class HttpChannelRequest:
    request: Request
    raw_body: bytes


class HttpChannelInboundMessage(TypedDict):
    user_id: str
    thread_id: str
    content: MessageContent
    target: JsonValue


class HttpPostedMessage(TypedDict):
    id: str
    url: NotRequired[str]


class _ContentPostInput(TypedDict):
    type: Literal["content"]
    content: MessageContent
    target: JsonValue
    new_thread: NotRequired[bool]


class _NativePostInput(TypedDict):
    type: Literal["native"]
    native: JsonValue
    target: JsonValue
    new_thread: NotRequired[bool]


HttpChannelPostInput = _ContentPostInput | _NativePostInput
HttpPost = Callable[[HttpChannelPostInput], Awaitable[HttpPostedMessage]]
HttpOnError = Callable[[Exception, "JsonValue"], Awaitable[None]]


class _MessageResultRequired(TypedDict):
    type: Literal["message"]
    message: HttpChannelInboundMessage


class HttpChannelMessageResult(_MessageResultRequired, total=False):
    response: Response
    raw_event: JsonValue


class _IgnoreResultRequired(TypedDict):
    type: Literal["ignore"]


class HttpChannelIgnoreResult(_IgnoreResultRequired, total=False):
    response: Response


HttpChannelParseResult = HttpChannelMessageResult | HttpChannelIgnoreResult
VerifyHttpRequest = Callable[[HttpChannelRequest], bool | Awaitable[bool]]
ParseHttpRequest = Callable[
    [HttpChannelRequest], HttpChannelParseResult | Awaitable[HttpChannelParseResult]
]
