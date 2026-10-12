"""Open SWE's agent tools as a remote MCP server for agents running elsewhere.

A person connects an MCP client (Claude Code, Claude.ai, Cursor) to ``/oswe/mcp``
and signs in with GitHub through the dashboard's GitHub App. Each request lists
and runs the tools that person's ``ToolCaller`` offers, so the catalog follows
their admin status and integration MCPs without any session state.
"""

import json
import logging
from collections.abc import Sequence
from typing import Final, NamedTuple, override

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.github import GitHubProvider, GitHubTokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.server.providers import Provider
from fastmcp.tools import Tool, ToolResult
from fastmcp.utilities.components import FastMCPComponent
from pydantic import JsonValue
from starlette.routing import BaseRoute, Route

from mcp.types import TextContent
from openswe.config import ENV
from openswe.dashboard.dev_login import dev_login_enabled
from openswe.dashboard.oauth import AUTH_PATH, GITHUB_CALLBACK_PATH, MCP_SIGN_IN_CALLBACK_PATH
from openswe.mcp.caller import ToolCaller
from openswe.mcp.token_store import SealedStore
from openswe.utils.dashboard_links import dashboard_api_base_url

logger = logging.getLogger(__name__)

PREFIX: Final = "/oswe"
_MCP_PATH: Final = "/mcp"
_SERVER_NAME: Final = "oswe"
_GITHUB_CACHE_SECONDS: Final = 300


async def _caller() -> ToolCaller:
    token = get_access_token()
    subject = token.claims.get("sub") if token is not None else None
    if not isinstance(subject, str) or not subject:
        raise ToolError("This request carried no GitHub identity")
    try:
        return await ToolCaller.for_github_account(subject)
    except PermissionError as exc:
        raise ToolError(str(exc)) from None


class _CallerTool(Tool):
    @override
    async def run(self, arguments: dict[str, JsonValue]) -> ToolResult:
        caller = await _caller()
        try:
            result = await caller.invoke(self.name, arguments)
        except Exception:
            logger.exception("MCP tool call failed", extra={"tool_name": self.name})
            raise
        text = result.content if isinstance(result.content, str) else json.dumps(result.content)
        return ToolResult(
            content=[TextContent(type="text", text=text)], is_error=result.status == "error"
        )


class _CallerProvider(Provider):
    """The tools the signed-in person may call, resolved on every request."""

    @override
    async def _list_tools(self) -> Sequence[Tool]:
        from openswe.sandboxes.tool_runtime import tool_parameters

        tools = await (await _caller()).tools()
        return [
            _CallerTool(name=name, description=tool.description, parameters=tool_parameters(tool))
            for name, (tool, _access) in sorted(tools.items())
        ]

    @override
    async def get_tasks(self) -> Sequence[FastMCPComponent]:
        # Startup asks outside any request; no caller tool runs as a background task.
        return []


class Mount(NamedTuple):
    """The app to mount at ``PREFIX``, and the routes served from the root.

    The root routes are RFC 8414 discovery and the sign-in endpoints, which live
    under the dashboard's auth path so GitHub returns to the App's one callback.
    """

    app: StarletteWithLifespan
    root_routes: list[BaseRoute]


def _auth() -> AuthProvider | None:
    client_id = ENV.GITHUB_APP_CLIENT_ID.optional()
    client_secret = ENV.GITHUB_APP_CLIENT_SECRET.optional()
    if client_id and client_secret and ENV.TOKEN_ENCRYPTION_KEY.optional():
        return GitHubProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=dashboard_api_base_url() + AUTH_PATH,
            resource_base_url=dashboard_api_base_url() + PREFIX,
            redirect_path=GITHUB_CALLBACK_PATH.removeprefix(AUTH_PATH),
            client_storage=SealedStore(),
            cache_ttl_seconds=_GITHUB_CACHE_SECONDS,
        )
    if dev_login_enabled():
        # Local dev has no GitHub App: callers send their own GitHub token, e.g. `gh auth token`.
        return GitHubTokenVerifier(cache_ttl_seconds=_GITHUB_CACHE_SECONDS)
    return None


def build_mount() -> Mount | None:
    auth = _auth()
    if auth is None:
        return None
    server = FastMCP(_SERVER_NAME, providers=[_CallerProvider()], auth=auth)
    # The only provider is the caller's catalog: a refusal must reach the client,
    # not list as an empty catalog.
    server.provider_error_strategy = "raise"
    app = server.http_app(path=_MCP_PATH, json_response=True, stateless_http=True)
    return Mount(
        app=app,
        root_routes=[*auth.get_well_known_routes(mcp_path=_MCP_PATH), *_sign_in_routes(auth)],
    )


def _sign_in_routes(auth: AuthProvider) -> list[BaseRoute]:
    callback = GITHUB_CALLBACK_PATH.removeprefix(AUTH_PATH)
    return [
        Route(
            MCP_SIGN_IN_CALLBACK_PATH if route.path == callback else AUTH_PATH + route.path,
            route.endpoint,
            methods=route.methods,
            name=route.name,
        )
        for route in auth.get_routes(mcp_path=_MCP_PATH)
        if isinstance(route, Route) and not route.path.startswith("/.well-known/")
    ]
