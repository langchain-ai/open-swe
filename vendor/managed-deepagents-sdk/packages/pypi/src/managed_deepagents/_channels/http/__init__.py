from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from langgraph_sdk.schema import MultitaskStrategy

from managed_deepagents._channel import Channel, ChannelEventsContext
from managed_deepagents._channels.http.threads import channel_thread_id
from managed_deepagents._channels.http.types import (
    ChannelName,
    HttpChannelCancelMessage,
    HttpChannelInboundMessage,
    HttpChannelParseResult,
    HttpChannelPostInput,
    HttpChannelRequest,
    HttpDelivery,
    HttpFinish,
    HttpFinishedDelivery,
    HttpFinishedRun,
    HttpInput,
    HttpOnError,
    HttpOnFinish,
    HttpOnReceived,
    HttpPost,
    HttpPostedMessage,
    HttpRunStatus,
    HttpSkipReason,
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
        provider_config: dict[str, JsonValue] | None = None,
        verify: VerifyHttpRequest,
        parse: ParseHttpRequest,
        post: HttpPost | None = None,
        on_error: HttpOnError | None = None,
        input: HttpInput | None = None,
        multitask_strategy: MultitaskStrategy = "enqueue",
        on_received: HttpOnReceived | None = None,
        on_finish: HttpOnFinish | None = None,
    ) -> None:
        if multitask_strategy not in _MULTITASK_STRATEGIES:
            raise TypeError(
                "channel multitask_strategy must be enqueue, interrupt, rollback or reject"
            )
        self.multitask_strategy: MultitaskStrategy = multitask_strategy
        self.provider_config = provider_config or {}
        self.kind = _nonblank(provider, "channels.http provider")
        self.verify = verify
        self.parse = parse
        self.post = post
        self.on_error = on_error
        self.input = input
        self.on_received = on_received
        self.on_finish = on_finish
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
            "providerConfig": self.provider_config,
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
    provider_config: dict[str, JsonValue] | None = None,
    verify: VerifyHttpRequest,
    parse: ParseHttpRequest,
    post: HttpPost | None = None,
    on_error: HttpOnError | None = None,
    input: HttpInput | None = None,
    multitask_strategy: MultitaskStrategy = "enqueue",
    on_received: HttpOnReceived | None = None,
    on_finish: HttpOnFinish | None = None,
) -> HttpChannel:
    """An HTTP channel.

    ``multitask_strategy`` is what a message that starts a turn does while its
    thread already runs: ``enqueue`` (the default) waits for the run and starts
    after it, ``interrupt`` cancels the run, keeping its progress, and starts at
    once, ``rollback`` cancels it and discards its progress, and ``reject``
    starts nothing. Messages to a thread that waits on an interrupt start
    nothing either way.

    ``on_received`` hears about every message that reaches a thread, before
    MDA decides what it does, for providers that expect a prompt
    acknowledgement. ``on_finish`` then hears once it is done: with the run it
    started and LangGraph's status for how that run ended (after ``on_error``
    for ``error`` and ``timeout``), or no ``run`` when it started none, and the
    interrupts the thread waits on. A ``parse`` result of type ``cancel``
    cancels the thread's runs and finishes without a ``run``.
    """
    return HttpChannel(
        provider=provider,
        provider_config=provider_config,
        verify=verify,
        parse=parse,
        post=post,
        on_error=on_error,
        input=input,
        multitask_strategy=multitask_strategy,
        on_received=on_received,
        on_finish=on_finish,
    )


_MULTITASK_STRATEGIES = frozenset({"enqueue", "interrupt", "rollback", "reject"})


def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


__all__ = [
    "ChannelName",
    "HttpChannel",
    "HttpChannelCancelMessage",
    "HttpChannelInboundMessage",
    "HttpChannelParseResult",
    "HttpChannelPostInput",
    "HttpChannelRequest",
    "HttpDelivery",
    "HttpFinish",
    "HttpFinishedDelivery",
    "HttpFinishedRun",
    "HttpPostedMessage",
    "HttpRunStatus",
    "HttpSkipReason",
    "channel_thread_id",
    "http_channel",
]
