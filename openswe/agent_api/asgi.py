"""ASGI plumbing for answering Agent Server requests in middleware or passing them on."""

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

import httpx
from pydantic import JsonValue, TypeAdapter
from starlette.datastructures import Headers
from starlette.types import Message, Receive, Scope, Send

_FORWARDED_REQUEST_HEADERS: Final = ("content-type", "accept", "last-event-id")
_DROPPED_RESPONSE_HEADERS: Final = frozenset(
    {"content-length", "transfer-encoding", "connection", "content-encoding", "keep-alive"}
)
_object = TypeAdapter(dict[str, JsonValue])


def configurable_of(container: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """``config.configurable`` of a run-create body, or an empty one."""
    config = container.get("config")
    configurable = config.get("configurable") if isinstance(config, dict) else None
    return _object.validate_python(configurable) if isinstance(configurable, dict) else {}


async def read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            break
    return b"".join(chunks)


def replay(body: bytes, receive: Receive) -> Receive:
    """A ``receive`` that hands an already-read body to the next app, then the client's own."""
    sent = False

    async def replayed() -> Message:
        nonlocal sent
        if sent:
            # Later calls wait on the client, so the next app still hears it disconnect.
            return await receive()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replayed


def json_body(body: bytes) -> dict[str, JsonValue] | None:
    """The request's JSON object; ``None`` when it is not one."""
    if not body.strip():
        return {}
    try:
        return _object.validate_json(body)
    except ValueError:
        return None


async def send_json(send: Send, status: int, payload: object) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def remote_path(scope: Scope) -> str:
    path: str = scope["path"]
    return path.removeprefix("/noauth")


async def forward(
    http: httpx.AsyncClient, scope: Scope, send: Send, body: bytes, *, path: str | None = None
) -> None:
    """Send the request to another deployment, and stream its answer back unchanged."""
    headers = Headers(scope=scope)
    request = http.build_request(
        scope["method"],
        path or remote_path(scope),
        params=scope.get("query_string", b"").decode(),
        headers={
            name: value for name in _FORWARDED_REQUEST_HEADERS if (value := headers.get(name))
        },
        content=body or None,
    )
    response = await http.send(request, stream=True)
    try:
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": [
                    (name.encode(), value.encode())
                    for name, value in response.headers.multi_items()
                    if name.lower() not in _DROPPED_RESPONSE_HEADERS
                ],
            }
        )
        # Decoded, since Content-Encoding is not passed on.
        async for chunk in response.aiter_bytes():
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})
    finally:
        await response.aclose()


class StatusRecorder:
    """A ``send`` that remembers the response status, and acts on a success before its body.

    With ``keep_body`` it keeps the body too, for a short JSON answer read afterwards.
    """

    def __init__(
        self,
        send: Send,
        on_success: Callable[[], Awaitable[None]] | None = None,
        *,
        keep_body: bool = False,
    ) -> None:
        self._send = send
        self._on_success = on_success
        self._keep_body = keep_body
        self._body = bytearray()
        self.status: int | None = None

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = int(message["status"])
            if self.succeeded and self._on_success is not None:
                await self._on_success()
        elif message["type"] == "http.response.body" and self._keep_body:
            self._body.extend(message.get("body", b""))
        await self._send(message)

    @property
    def body(self) -> bytes:
        return bytes(self._body)

    @property
    def succeeded(self) -> bool:
        return self.status is not None and 200 <= self.status < 300
