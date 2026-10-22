"""A route class that caps how much request body a caller may send."""

from typing import ClassVar

from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.types import Message, Receive, Scope, Send


class BodyLimitedRoute(APIRoute):
    """Rejects a request with 413 once its body passes ``max_bytes``, while it streams in."""

    max_bytes: ClassVar[int] = 1024 * 1024
    too_large: ClassVar[str] = "Request is too large"

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        size = 0

        async def limited_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > self.max_bytes:
                    raise HTTPException(413, self.too_large)
            return message

        await super().handle(scope, limited_receive, send)
