"""The tool server remote runtimes call: MCP at ``/remote-runtime/mcp``, behind the run token.

Stateless with JSON responses, so any replica answers any call. The middleware
verifies the bearer token once per request and leaves the verified run for the
handlers, which pick the catalog of the graph the token was signed for and read
the thread and run configuration from it, never from arguments.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Final, NamedTuple

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import JsonValue
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent.remote_runtime import reviewer
from agent.remote_runtime.tokens import RemoteRun, RuntimeTokenError, verify_runtime_token

logger = logging.getLogger(__name__)

PATH: Final = "/remote-runtime/mcp"
_SERVER_NAME: Final = "open-swe-remote-runtime"
_RUN_STATE_KEY: Final = "remote_run"

Call = Callable[[RemoteRun, str, Mapping[str, JsonValue]], Awaitable[dict[str, JsonValue]]]


class Catalog(NamedTuple):
    tools: Callable[[], list[types.Tool]]
    call: Call


def _reviewer_tools() -> list[types.Tool]:
    hooks = [
        types.Tool(name=name, inputSchema={"type": "object", "properties": {}})
        for name in sorted(reviewer.RUNTIME_HOOKS)
    ]
    return [
        *(
            types.Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["parameters"],
            )
            for spec in reviewer.runtime_spec()["tools"]
        ),
        *hooks,
    ]


_CATALOGS: Final[Mapping[str, Catalog]] = {
    reviewer.ASSISTANT_ID: Catalog(tools=_reviewer_tools, call=reviewer.call),
}


def _verified_run(server: Server[object, Request]) -> tuple[RemoteRun, Catalog]:
    request = server.request_context.request
    run = request.scope.get("state", {}).get(_RUN_STATE_KEY) if request is not None else None
    if not isinstance(run, RemoteRun):
        raise PermissionError("The call carried no verified run")
    catalog = _CATALOGS.get(run.assistant_id)
    if catalog is None:
        raise PermissionError(f"No remote runtime serves {run.assistant_id}")
    return run, catalog


def build_server() -> Server[object, Request]:
    server: Server[object, Request] = Server(_SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        _, catalog = _verified_run(server)
        return catalog.tools()

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        run, catalog = _verified_run(server)
        try:
            return await catalog.call(run, name, arguments)
        except Exception:
            logger.exception(
                "Remote runtime call failed",
                extra={"tool_name": name, "thread_id": run.thread_id},
            )
            raise

    return server


async def _refuse(scope: Scope, receive: Receive, send: Send, detail: str, challenge: str) -> None:
    response = JSONResponse(
        {"detail": detail}, status_code=401, headers={"WWW-Authenticate": challenge}
    )
    await response(scope, receive, send)


class RunTokenMiddleware:
    """Verify the run token on every request and leave the verified run for the handlers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scheme, _, token = Request(scope).headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            await _refuse(scope, receive, send, "A run token is required", "Bearer")
            return
        try:
            run = verify_runtime_token(token.strip())
        except RuntimeTokenError as exc:
            logger.info("Remote runtime call refused", extra={"refusal": str(exc)})
            await _refuse(
                scope,
                receive,
                send,
                "The run token was not accepted",
                'Bearer error="invalid_token"',
            )
            return
        scope.setdefault("state", {})[_RUN_STATE_KEY] = run
        await self.app(scope, receive, send)


class Mount(NamedTuple):
    """The ASGI app to route at ``PATH`` and the lifespan the app holds open while it serves."""

    app: ASGIApp
    lifespan: Callable[[], AbstractAsyncContextManager[None]]


def build_mount() -> Mount:
    manager = StreamableHTTPSessionManager(
        app=build_server(), event_store=None, json_response=True, stateless=True
    )
    return Mount(app=RunTokenMiddleware(manager.handle_request), lifespan=manager.run)
