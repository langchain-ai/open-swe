"""What remote runtimes call back into, behind the run token.

The model's tools are an MCP server at ``/remote-runtime/mcp``; run hooks are
plain ``POST /remote-runtime/hooks/{hook}`` routes. Both are stateless, so any
replica answers any call. The middleware verifies the bearer token once per
request and leaves the verified run for the handlers, which pick the catalog of
the graph the token was signed for and read the thread and run configuration
from it, never from arguments.
"""

import json
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

from openswe.remote_runtime import reviewer
from openswe.remote_runtime.tokens import RemoteRun, RuntimeTokenError, verify_runtime_token

logger = logging.getLogger(__name__)

PATH: Final = "/remote-runtime/mcp"
HOOKS_PATH: Final = "/remote-runtime/hooks/{hook}"
_SERVER_NAME: Final = "open-swe-remote-runtime"
_RUN_STATE_KEY: Final = "remote_run"

Call = Callable[[RemoteRun, str, Mapping[str, JsonValue]], Awaitable[reviewer.ToolResult]]
Hook = Callable[[RemoteRun, str], Awaitable[JsonValue]]


class Catalog(NamedTuple):
    tools: Callable[[], list[types.Tool]]
    call: Call
    hook: Hook


def _reviewer_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=spec["name"],
            description=spec["description"],
            inputSchema=spec["parameters"],
        )
        for spec in reviewer.served_tools()
    ]


_CATALOGS: Final[Mapping[str, Catalog]] = {
    reviewer.ASSISTANT_ID: Catalog(
        tools=_reviewer_tools, call=reviewer.call_tool, hook=reviewer.run_hook
    ),
}


def _catalog(run: RemoteRun) -> Catalog:
    catalog = _CATALOGS.get(run.assistant_id)
    if catalog is None:
        raise PermissionError(f"No remote runtime serves {run.assistant_id}")
    return catalog


def _scope_run(scope: Scope) -> RemoteRun:
    run = scope.get("state", {}).get(_RUN_STATE_KEY)
    if not isinstance(run, RemoteRun):
        raise PermissionError("The call carried no verified run")
    return run


def _verified_run(server: Server[object, Request]) -> tuple[RemoteRun, Catalog]:
    request = server.request_context.request
    if request is None:
        raise PermissionError("The call carried no verified run")
    run = _scope_run(request.scope)
    return run, _catalog(run)


def build_server() -> Server[object, Request]:
    server: Server[object, Request] = Server(_SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        _, catalog = _verified_run(server)
        return catalog.tools()

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, JsonValue]) -> types.CallToolResult:
        run, catalog = _verified_run(server)
        try:
            result = await catalog.call(run, name, arguments)
        except Exception:
            logger.exception(
                "Remote runtime call failed",
                extra={"tool_name": name, "thread_id": run.thread_id},
            )
            raise
        text = result.content if isinstance(result.content, str) else json.dumps(result.content)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=result.status == "error",
        )

    return server


async def _hook_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    run = _scope_run(scope)
    hook = scope["path_params"]["hook"]
    try:
        result = await _catalog(run).hook(run, hook)
    except reviewer.UnknownHookError:
        await JSONResponse({"detail": f"Unknown hook {hook}"}, status_code=404)(
            scope, receive, send
        )
        return
    except Exception:
        logger.exception(
            "Remote runtime hook failed", extra={"hook": hook, "thread_id": run.thread_id}
        )
        raise
    await JSONResponse(result)(scope, receive, send)


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
    """The ASGI apps to route at ``PATH`` and ``HOOKS_PATH``, and the lifespan they hold open."""

    app: ASGIApp
    hooks: ASGIApp
    lifespan: Callable[[], AbstractAsyncContextManager[None]]


def build_mount() -> Mount:
    manager = StreamableHTTPSessionManager(
        app=build_server(), event_store=None, json_response=True, stateless=True
    )
    return Mount(
        app=RunTokenMiddleware(manager.handle_request),
        hooks=RunTokenMiddleware(_hook_endpoint),
        lifespan=manager.run,
    )
