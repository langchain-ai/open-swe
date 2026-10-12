"""Correlate each dashboard request with the ID the browser shows when it fails."""

import logging
import re
from uuid import uuid4

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = b"x-request-id"
_REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DASHBOARD_API_PREFIX = "/dashboard/api/"
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _tag_root_span(tags: dict[str, str]) -> None:
    try:
        from ddtrace.trace import tracer  # pyright: ignore[reportMissingImports]
    except ImportError:
        from ddtrace import tracer  # pyright: ignore[reportMissingImports]

    span = tracer.current_root_span()
    if span is not None:
        span.set_tags(tags)


def _safe_tag(tags: dict[str, str]) -> None:
    try:
        _tag_root_span(tags)
    except Exception:
        logger.debug("Could not tag the APM trace with the request ID", exc_info=True)


def _request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER:
            candidate = value.decode("latin-1")
            if _REQUEST_ID_RE.match(candidate):
                return candidate
    return f"req_{uuid4()}"


class RequestIdMiddleware:
    """Tag the trace with the browser's request ID and log dashboard failures under it.

    Failed writes and every 5xx are logged with ``error_id`` so the ID in the
    dashboard's error toast finds the server side of the failure.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        _safe_tag({"request_id": request_id})
        method = scope.get("method", "GET")
        path = scope.get("path", "")

        def failure_extra(status_code: int) -> dict[str, str | int]:
            route = getattr(scope.get("route"), "path", None)
            return {
                "error_id": request_id,
                "http_method": method,
                "http_route": route if isinstance(route, str) else path,
                "status_code": status_code,
            }

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (REQUEST_ID_HEADER, request_id.encode("latin-1")),
                ]
                if status_code >= 400:
                    _safe_tag({"error_id": request_id})
                if path.startswith(_DASHBOARD_API_PREFIX) and (
                    status_code >= 500 or (status_code >= 400 and method not in _READ_METHODS)
                ):
                    logger.warning("Dashboard request failed", extra=failure_extra(status_code))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except BaseException:
            _safe_tag({"error_id": request_id})
            if path.startswith(_DASHBOARD_API_PREFIX):
                logger.exception("Dashboard request raised", extra=failure_extra(500))
            raise


def add_request_ids(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
