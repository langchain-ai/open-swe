"""Dashboard route class that keeps submitted MCP credentials out of validation errors."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

_VALIDATION_MESSAGES = {
    "name": (
        "Connection name must start with a lowercase letter and contain only lowercase "
        "letters, numbers, hyphens, or underscores (1-32 characters); for example, incident"
    ),
    "url": (
        "Server URL must be HTTPS, at most 2048 characters, and contain no credentials, "
        "whitespace, or fragments; put authentication in headers"
    ),
    "transport": "Transport must be Streamable HTTP or SSE",
    "enabled": "Enabled must be true or false",
    "headers": (
        "Headers must have valid, unique names and plain-text values without line breaks; "
        "use at most 20 headers and 8192 characters per value"
    ),
    "allowed_tools": "Allowed tools must be a list of non-empty names (1-128 characters)",
    "oauth": "OAuth requires an HTTPS token URL, client ID, and client secret; use the client_credentials grant",
}


class MCPRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def redacted_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Error messages and nested locations can include submitted credentials.
                messages = dict.fromkeys(
                    _VALIDATION_MESSAGES.get(
                        error["loc"][1] if len(error["loc"]) > 1 else "",
                        "Invalid MCP connection settings",
                    )
                    for error in exc.errors()
                )
                return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})

        return redacted_handler
