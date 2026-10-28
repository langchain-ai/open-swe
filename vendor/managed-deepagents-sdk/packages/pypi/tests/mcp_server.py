"""Loopback MCP server; identities and bearer material are synthetic."""

from __future__ import annotations

import asyncio
import json
import socket
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import uvicorn
from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import (
    CallToolResult,
    ElicitRequest,
    ElicitRequestFormParams,
    ImageContent,
    InputRequiredResult,
    TextContent,
)
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass
class Observation:
    lists: Counter[str] = field(default_factory=Counter)
    calls: Counter[str] = field(default_factory=Counter)
    peers: set[tuple[str, int]] = field(default_factory=set)
    attempts: int = 0
    wire_methods: Counter[str] = field(default_factory=Counter)
    offline: bool = False
    auth_denied: bool = False
    auth_status: int = 401
    ttl: int | None = 60_000
    generation: str | None = None
    fail_catalog: bool = False
    catalog_delay: float = 0
    discover_delay: float = 0
    legacy_only: bool = False
    slow_entered: asyncio.Event = field(default_factory=asyncio.Event)
    slow_release: asyncio.Event = field(default_factory=asyncio.Event)


class ObserveCatalog(Middleware):
    def __init__(self, observation: Observation) -> None:
        self.observation = observation

    async def on_discover(
        self, context: MiddlewareContext, call_next: Callable[..., Awaitable[Any]]
    ) -> Any:
        if self.observation.discover_delay:
            await asyncio.sleep(self.observation.discover_delay)
        return await call_next(context)

    async def on_list_tools(
        self, context: MiddlewareContext, call_next: Callable[..., Awaitable[Any]]
    ) -> Any:
        owner = get_http_request().headers.get("x-spike-principal", "anonymous")
        self.observation.lists[owner] += 1
        if self.observation.fail_catalog:
            raise RuntimeError("synthetic catalog availability failure")
        if self.observation.catalog_delay:
            await asyncio.sleep(self.observation.catalog_delay)
        tools = await call_next(context)
        return [
            tool
            for tool in tools
            if not tool.name.startswith("only_") or tool.name == f"only_{owner}"
        ]

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: Callable[..., Awaitable[Any]]
    ) -> Any:
        self.observation.calls[context.message.name] += 1
        return await call_next(context)


class ObserveHTTP:
    def __init__(self, app: ASGIApp, observation: Observation) -> None:
        self.app = app
        self.observation = observation

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: C901 - fixture observation and wire mutations.
        if scope["type"] == "http":
            self.observation.attempts += 1
            method = dict(scope["headers"]).get(b"mcp-method", b"").decode()
            self.observation.wire_methods[method] += 1
            if method == "server/discover" and self.observation.legacy_only:
                await PlainTextResponse("legacy peer", status_code=404)(scope, receive, send)
                return
            self.observation.peers.add(scope["client"])
            if self.observation.auth_denied:
                await PlainTextResponse(
                    "synthetic revoked grant", status_code=self.observation.auth_status
                )(scope, receive, send)
                return
            if self.observation.offline:
                await PlainTextResponse("synthetic outage", status_code=503)(scope, receive, send)
                return

        async def send_with_cache_hint(message: dict[str, Any]) -> None:
            if (
                scope["type"] == "http"
                and dict(scope["headers"]).get(b"mcp-method") == b"tools/list"
            ):
                if message["type"] == "http.response.start":
                    message = {
                        **message,
                        "headers": [
                            (key, value)
                            for key, value in message.get("headers", [])
                            if key.lower() != b"content-length"
                        ],
                    }
                elif message["type"] == "http.response.body" and message.get("body"):
                    payload = json.loads(message["body"])
                    result = payload.get("result")
                    if isinstance(result, dict):
                        if self.observation.ttl is None:
                            result.pop("ttlMs", None)
                            result.pop("cacheScope", None)
                        else:
                            result["ttlMs"] = self.observation.ttl
                            result["cacheScope"] = "public"
                        message = {**message, "body": json.dumps(payload).encode()}
            await send(message)

        async def receive_with_method() -> dict[str, Any]:
            message = await receive()
            if scope["type"] == "http" and message.get("body"):
                payload = json.loads(message["body"])
                if isinstance(payload, dict) and "method" in payload:
                    method = payload["method"]
                    if not dict(scope["headers"]).get(b"mcp-method"):
                        self.observation.wire_methods[method] += 1
            return message

        await self.app(scope, receive_with_method, send_with_cache_hint)


@dataclass
class Server:
    url: str
    observation: Observation


def fixture_server(  # noqa: C901 - colocated tools form one test server.
    observation: Observation, *, extra_name: str | None = None, ttl: int | None = 60_000
) -> FastMCP:
    # Mark catalogs public deliberately: client-side partitioning must still win.
    server = FastMCP(
        "mda-spike",
        middleware=[ObserveCatalog(observation)],
        cache_ttl=60_000,
        cache_scope="public",
    )

    @server.tool
    def echo(value: str) -> str:
        """Return a value."""
        return value

    @server.tool
    def whoami() -> str:
        """Return synthetic transport identity and token generation."""
        request = get_http_request()
        owner = request.headers.get("x-spike-principal", "anonymous")
        authorization = request.headers.get("authorization", "")
        generation = observation.generation or request.headers.get("x-spike-generation", "1")
        expected = f"Bearer synthetic-{owner}-{generation}"
        assert authorization == expected
        return f"{owner}:{generation}"

    @server.tool
    def rich() -> CallToolResult:
        """Return native multimodal content and a structured artifact."""
        return CallToolResult(
            content=[
                TextContent(type="text", text="rich"),
                ImageContent(type="image", data="c3ludGhldGljLWltYWdl", mimeType="image/png"),
            ],
            structuredContent={"answer": 42},
        )

    @server.tool
    async def slow() -> str:
        """Wait until the fixture releases this request."""
        observation.slow_entered.set()
        await observation.slow_release.wait()
        return "released"

    @server.tool
    def fail() -> str:
        """Report an upstream tool error."""
        raise ValueError("synthetic upstream tool failure")

    @server.tool
    def ask(ctx: Context) -> str | InputRequiredResult:
        """Ask for one value using modern request-and-reenter elicitation."""
        responses = ctx.input_responses
        if not responses:
            return InputRequiredResult(
                inputRequests={
                    "answer": ElicitRequest(
                        params=ElicitRequestFormParams(
                            message="Provide a fixture value",
                            requestedSchema={
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        )
                    )
                },
                requestState="fixture-round",
            )
        assert ctx.request_state == "fixture-round"
        return str(responses["answer"].content["value"])

    @server.tool
    async def legacy_ask(ctx: Context) -> str:
        """Ask through the legacy server-pushed elicitation path."""
        answer = await ctx.elicit("Provide a fixture value", str)
        return str(answer)

    for owner in ("alice", "bob", "anonymous"):

        def visible() -> str:
            """A caller-scoped catalog entry."""
            return "visible"

        server.tool(visible, name=f"only_{owner}")
    if extra_name:
        server.tool(echo, name=extra_name)
    return server


@asynccontextmanager
async def serve(
    *,
    transport: Literal["http", "sse"] = "http",
    extra_name: str | None = None,
    ttl: int | None = 60_000,
    legacy_only: bool = False,
) -> AsyncIterator[Server]:
    observation = Observation(ttl=ttl, legacy_only=legacy_only)
    mcp = fixture_server(observation, extra_name=extra_name, ttl=ttl)
    path = "/mcp" if transport == "http" else "/sse"
    app = mcp.http_app(
        path=path,
        transport=transport,
        json_response=True,
        stateless_http=transport == "http",
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    host, port = listener.getsockname()
    config = uvicorn.Config(
        ObserveHTTP(app, observation),
        log_level="critical",
        access_log=False,
        lifespan="on",
        timeout_graceful_shutdown=1,
    )
    runner = uvicorn.Server(config)
    task = asyncio.create_task(runner.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not runner.started:
                if task.done():
                    await task
                    raise RuntimeError("loopback server exited before startup")
                await asyncio.sleep(0.01)
        yield Server(f"http://{host}:{port}{path}", observation)
    finally:
        observation.slow_release.set()
        runner.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            listener.close()
