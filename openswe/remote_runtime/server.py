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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from functools import cache
from typing import Final, Literal, NamedTuple, override

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool
from fastmcp.tools import ToolResult as MCPToolResult
from fastmcp.utilities.components import FastMCPComponent
from mcp.types import TextContent
from pydantic import BaseModel, JsonValue
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openswe.remote_runtime.tokens import RemoteRun, RuntimeTokenError, verify_runtime_token

logger = logging.getLogger(__name__)

PATH: Final = "/remote-runtime/mcp"
HOOKS_PATH: Final = "/remote-runtime/hooks/{hook}"
_SERVER_NAME: Final = "open-swe-remote-runtime"
_RUN_STATE_KEY: Final = "remote_run"


class UnknownHookError(LookupError):
    """A remote graph called a run hook this backend does not serve."""


class ToolResult(BaseModel):
    status: Literal["success", "error"]
    content: JsonValue


Call = Callable[[RemoteRun, str, Mapping[str, JsonValue]], Awaitable[ToolResult]]
Hook = Callable[[RemoteRun, str], Awaitable[JsonValue]]


class Catalog(NamedTuple):
    tools: Callable[[], Sequence[Tool]]
    call: Call
    hook: Hook


@cache
def _catalogs() -> Mapping[str, Catalog]:
    # The webapp must not import the agent stack at startup.
    from openswe.remote_runtime import reviewer

    def tools() -> list[Tool]:
        return [
            _CatalogTool(
                name=spec["name"], description=spec["description"], parameters=spec["parameters"]
            )
            for spec in reviewer.served_tools()
        ]

    return {
        reviewer.ASSISTANT_ID: Catalog(tools=tools, call=reviewer.call_tool, hook=reviewer.run_hook)
    }


def _catalog(run: RemoteRun) -> Catalog:
    catalog = _catalogs().get(run.assistant_id)
    if catalog is None:
        raise PermissionError(f"No remote runtime serves {run.assistant_id}")
    return catalog


def _scope_run(scope: Scope) -> RemoteRun:
    run = scope.get("state", {}).get(_RUN_STATE_KEY)
    if not isinstance(run, RemoteRun):
        raise PermissionError("The call carried no verified run")
    return run


def _verified_run() -> tuple[RemoteRun, Catalog]:
    run = _scope_run(get_http_request().scope)
    return run, _catalog(run)


class _CatalogTool(Tool):
    @override
    async def run(self, arguments: dict[str, JsonValue]) -> MCPToolResult:
        run, catalog = _verified_run()
        try:
            result = await catalog.call(run, self.name, arguments)
        except Exception:
            logger.exception(
                "Remote runtime call failed",
                extra={"tool_name": self.name, "thread_id": run.thread_id},
            )
            raise
        text = result.content if isinstance(result.content, str) else json.dumps(result.content)
        return MCPToolResult(
            content=[TextContent(type="text", text=text)], is_error=result.status == "error"
        )


class _CatalogProvider(Provider):
    """The tools of the catalog the request's verified run token was signed for."""

    @override
    async def _list_tools(self) -> Sequence[Tool]:
        _, catalog = _verified_run()
        return catalog.tools()

    @override
    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        # Startup asks outside any request; no catalog tool runs as a background task.
        return []


def build_server() -> FastMCP:
    return FastMCP(_SERVER_NAME, providers=[_CatalogProvider()])


async def _hook_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    run = _scope_run(scope)
    hook = scope["path_params"]["hook"]
    try:
        result = await _catalog(run).hook(run, hook)
    except UnknownHookError:
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
    lifespan: Callable[[], AbstractAsyncContextManager[object]]


def build_mount() -> Mount:
    app = build_server().http_app(path=PATH, json_response=True, stateless_http=True)
    return Mount(
        app=RunTokenMiddleware(app),
        hooks=RunTokenMiddleware(_hook_endpoint),
        lifespan=lambda: app.lifespan(app),
    )
