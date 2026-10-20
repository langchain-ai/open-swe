from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from managed_deepagents._channel import Channel, ChannelEventsContext
from managed_deepagents._channels.http.types import (
    HttpChannelInboundMessage,
    HttpChannelParseResult,
    HttpChannelPostInput,
    HttpChannelRequest,
    HttpOnError,
    HttpPost,
    HttpPostedMessage,
    ParseHttpRequest,
    VerifyHttpRequest,
)

if TYPE_CHECKING:
    from starlette.responses import Response

    from managed_deepagents._channels.http.types import JsonValue
    from managed_deepagents._channels.manifest import ChannelManifest
    from managed_deepagents._channels.types import ChannelMessage, PostedChannelMessage


class HttpChannel(Channel):
    def __init__(
        self,
        *,
        provider: str,
        verify: VerifyHttpRequest,
        parse: ParseHttpRequest,
        post: HttpPost | None = None,
        on_error: HttpOnError | None = None,
    ) -> None:
        self.kind = _nonblank(provider, "channels.http provider")
        self.verify = verify
        self.parse = parse
        self.post = post
        self.on_error = on_error
        self.messaging = self._build_messaging if post is not None else None

    def _build_messaging(self, _env: Mapping[str, str]) -> _HttpReplyTransport | None:
        if self.post is None:
            return None
        return _HttpReplyTransport(self.post)

    async def events(self, ctx: ChannelEventsContext) -> Response:
        from managed_deepagents._channels.http.http import handle_http_request  # noqa: PLC0415

        return await handle_http_request(ctx, self)

    def requirements(self, name: str) -> ChannelManifest:
        return {
            "name": name,
            "provider": self.kind,
            "eventsPath": f"/channels/{name}/events",
            "autoReply": self.post is not None,
            "requiredPermissions": [],
            "requiredEnv": [],
            "providerConfig": {},
        }


class _HttpReplyTransport:
    def __init__(self, post: HttpPost) -> None:
        self._post = post

    async def post(
        self, *, message: ChannelMessage, target: JsonValue, new_thread: bool | None = None
    ) -> PostedChannelMessage:
        if message["type"] == "content":
            posted = await self._post(
                {
                    "type": "content",
                    "content": message["content"],
                    "target": target,
                    **({"new_thread": new_thread} if new_thread is not None else {}),
                }
            )
        else:
            posted = await self._post(
                {
                    "type": "native",
                    "native": message["native"],
                    "target": target,
                    **({"new_thread": new_thread} if new_thread is not None else {}),
                }
            )
        result: PostedChannelMessage = {"id": posted["id"]}
        url = posted.get("url")
        if isinstance(url, str):
            result["url"] = url
        return result


def http_channel(
    *,
    provider: str,
    verify: VerifyHttpRequest,
    parse: ParseHttpRequest,
    post: HttpPost | None = None,
    on_error: HttpOnError | None = None,
) -> HttpChannel:
    return HttpChannel(
        provider=provider,
        verify=verify,
        parse=parse,
        post=post,
        on_error=on_error,
    )


def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


__all__ = [
    "HttpChannel",
    "HttpChannelInboundMessage",
    "HttpChannelParseResult",
    "HttpChannelPostInput",
    "HttpChannelRequest",
    "HttpPostedMessage",
    "http_channel",
]
