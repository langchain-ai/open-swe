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

        def name_after_routing() -> None:
            nonlocal named
            if named:
                return
            named = True
            route_path = getattr(scope.get("route"), "path", None)
            if not isinstance(route_path, str) or not route_path:
                return
            try:
                _rename_root_span(f"{scope.get('method', 'GET')} {route_path}")
            except Exception:
                logger.debug("Could not name the APM trace resource", exc_info=True)

        async def send_named(message: Message) -> None:
            if message["type"] == "http.response.start":
                name_after_routing()
            await send(message)

        try:
            await self.app(scope, receive, send_named)
        except BaseException:
            # The 500 is generated outside this middleware, so a raising endpoint
            # would otherwise keep the unnamed resource and hide route-level
            # errors from APM. The root span is still open while we unwind.
            name_after_routing()
            raise


def add_trace_resource_names(app: FastAPI) -> None:
    app.add_middleware(TraceResourceNameMiddleware)
