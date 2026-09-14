"""Name APM spans after the route that handled the request."""

import logging

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


def _rename_root_span(resource: str) -> None:
    try:
        from ddtrace.trace import tracer  # pyright: ignore[reportMissingImports]
    except ImportError:
        from ddtrace import tracer  # pyright: ignore[reportMissingImports]

    span = tracer.current_root_span()
    if span is not None:
        span.resource = resource


class TraceResourceNameMiddleware:
    """Give each request the resource name its route deserves.

    The platform tracer instruments the server this app is mounted behind, so it
    resolves no route for these paths: every dashboard request lands on a single
    ``GET`` resource, which cannot be searched, compared or alerted on in APM.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        named = False

        async def send_named(message: Message) -> None:
            nonlocal named
            if not named and message["type"] == "http.response.start":
                named = True
                route_path = getattr(scope.get("route"), "path", None)
                if isinstance(route_path, str) and route_path:
                    try:
                        _rename_root_span(f"{scope.get('method', 'GET')} {route_path}")
                    except Exception:
                        logger.debug("Could not name the APM trace resource", exc_info=True)
            await send(message)

        await self.app(scope, receive, send_named)


def add_trace_resource_names(app: FastAPI) -> None:
    app.add_middleware(TraceResourceNameMiddleware)
