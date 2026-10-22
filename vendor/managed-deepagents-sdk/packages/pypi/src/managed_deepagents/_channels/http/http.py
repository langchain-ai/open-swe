from __future__ import annotations

import copy
import inspect
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Mapping
from typing import TYPE_CHECKING, TypeVar, cast

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import Message

from managed_deepagents import _ingress
from managed_deepagents._channels.http import ingress as http_ingress
from managed_deepagents._channels.http.types import (
    HttpChannelParseResult,
    HttpChannelRequest,
)
from managed_deepagents._channels.runtime import is_json_value, json_copy

if TYPE_CHECKING:
    from managed_deepagents._channel import ChannelEventsContext
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.http import HttpChannel
    from managed_deepagents._channels.http.types import (
        HttpChannelInboundMessage,
        HttpOnError,
        HttpPost,
    )
    from managed_deepagents._channels.types import ChannelEvent, JsonValue, MessageContent
    from managed_deepagents._ingress_runs import StartedIngressRun

_LOG = logging.getLogger("mda")
_T = TypeVar("_T")


async def _resolve(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        return await cast("Awaitable[_T]", value)
    return cast("_T", value)


def _adapter_request(source: HttpChannelRequest) -> HttpChannelRequest:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return await source.request.receive()
        sent = True
        return {"type": "http.request", "body": source.raw_body, "more_body": False}

    return HttpChannelRequest(
        request=Request(dict(source.request.scope), receive=receive),
        raw_body=source.raw_body,
    )


async def _parse_request(
    request: HttpChannelRequest, channel: HttpChannel
) -> HttpChannelParseResult | Response:
    try:
        verified = await _resolve(channel.verify(_adapter_request(request)))
    except Exception as error:  # noqa: BLE001
        _LOG.error("channel verification failed (%s)", type(error).__name__)
        return _error(500, "channel verification failed")
    if not verified:
        return _error(401, "invalid channel signature")

    try:
        result = await _resolve(channel.parse(_adapter_request(request)))
    except Exception as error:  # noqa: BLE001
        _LOG.warning("channel payload rejected (%s)", type(error).__name__)
        return _error(400, "invalid channel payload")
    if (
        not isinstance(result, Mapping)
        or result.get("type") not in ("ignore", "message")
        or ("response" in result and not isinstance(result["response"], Response))
        or ("raw_event" in result and not is_json_value(result["raw_event"]))
    ):
        return _error(400, "invalid channel parse result")
    if result["type"] == "message" and not _valid_message(result.get("message")):
        return _error(400, "invalid channel message")
    return result


async def handle_http_request(ctx: ChannelEventsContext, channel: HttpChannel) -> Response:
    raw = ctx.get("raw_body")
    if isinstance(raw, str):
        raw = raw.encode()
    if not isinstance(raw, bytes):
        raise ValueError("managed channel route did not provide request bytes")
    request = HttpChannelRequest(request=cast("Request", ctx["request"]), raw_body=raw)
    result = await _parse_request(request, channel)
    if isinstance(result, Response):
        return result
    if result["type"] == "ignore":
        return result.get("response") or JSONResponse({"status": "ignored"}, status_code=202)

    message: HttpChannelInboundMessage = {
        **result["message"],
        "content": cast(
            "MessageContent",
            json_copy(cast("JsonValue", result["message"]["content"])),
        ),
        "target": json_copy(result["message"]["target"]),
    }
    assistant_id = os.environ.get("MDA_ASSISTANT_ID", "").strip()
    if not assistant_id:
        return _error(500, "channel runtime is not configured")
    delivery_id = str(uuid.uuid4())
    content = message["content"]
    thread_id = message["thread_id"].lower()
    source_thread_key = "http:v1:" + json.dumps(
        [ctx["name"], thread_id], ensure_ascii=False, separators=(",", ":")
    )
    channel_event: ChannelEvent = {
        "type": "message",
        "messages": [http_ingress.normalize_http_message(content)],
    }
    binding: ChannelBinding = {
        "name": ctx["name"],
        "provider": channel.kind,
        "source_thread_key": source_thread_key,
        "delivery_id": delivery_id,
        "transport": "direct",
        "target": message["target"],
        "event": channel_event,
    }
    if "raw_event" in result:
        binding["raw_event"] = json_copy(result["raw_event"])
    try:
        started = await http_ingress.start_http_ingress_agent(
            user_id=message["user_id"],
            thread_id=thread_id,
            source_thread_key=source_thread_key,
            source_provider=channel.kind,
            binding=copy.deepcopy(binding),
            input=copy.deepcopy(content),
            assistant_id=assistant_id,
        )
    except Exception as error:  # noqa: BLE001
        _LOG.error("channel run could not be started (%s)", type(error).__name__)
        return _error(500, "channel run could not be started")

    post = channel.post
    on_error = channel.on_error
    _ingress.schedule_ingress_work(
        lambda: _deliver_reply(
            binding, message["user_id"], source_thread_key, started, post, on_error
        ),
        label=f'channel "{ctx["name"]}" delivery',
    )
    return result.get("response") or JSONResponse(
        {"status": "accepted", "deliveryId": delivery_id}, status_code=202
    )


async def _deliver_reply(
    binding: ChannelBinding,
    user_id: str,
    source_thread_key: str,
    started: StartedIngressRun,
    post: HttpPost | None,
    on_error: HttpOnError | None,
) -> None:
    reply_attempted = False
    try:
        invocation = await http_ingress.observe_http_ingress_agent(
            user_id=user_id,
            source_thread_key=source_thread_key,
            source_provider=binding["provider"],
            **started,
        )
        if post is None or invocation["reply_content"] is None:
            return
        reply_attempted = True
        await post(
            {
                "type": "content",
                "content": cast(
                    "MessageContent",
                    json_copy(cast("JsonValue", invocation["reply_content"])),
                ),
                "target": json_copy(binding["target"]),
            }
        )
    except Exception as error:  # noqa: BLE001
        _LOG.error("channel delivery failed (%s)", type(error).__name__)
        try:
            if on_error is not None:
                await on_error(error, json_copy(binding["target"]))
            elif post is not None and not reply_attempted:
                await post(
                    {
                        "type": "content",
                        "content": "Something went wrong while processing your request.",
                        "target": json_copy(binding["target"]),
                    }
                )
        except Exception as reply_error:  # noqa: BLE001
            action = "callback" if on_error is not None else "reply"
            _LOG.error("channel error %s failed (%s)", action, type(reply_error).__name__)


def _valid_message(message: object) -> bool:
    if (
        not isinstance(message, Mapping)
        or "target" not in message
        or not is_json_value(message["target"])
    ):
        return False
    user_id = message.get("user_id")
    thread_id = message.get("thread_id")
    if not isinstance(user_id, str) or not user_id.strip():
        return False
    if not isinstance(thread_id, str) or not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", thread_id
    ):
        return False
    content = message.get("content")
    if not is_json_value(content):
        return False
    if isinstance(content, str):
        return bool(content.strip())
    return (
        isinstance(content, list)
        and bool(content)
        and all(
            isinstance(block, Mapping)
            and isinstance(block_type := block.get("type"), str)
            and block_type.strip()
            for block in content
        )
    )


def _error(status: int, message: str) -> Response:
    return JSONResponse({"error": message}, status_code=status)
