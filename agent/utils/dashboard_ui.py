"""Serve the dashboard from the backend's own origin.

A dashboard build (``ui/.output/public``: a client-only ``_shell.html`` plus
hashed assets) mounted at ``/`` lets one LangGraph deployment serve both the API
and the UI, so the browser reaches ``/dashboard/api/*`` with relative URLs and no
cross-origin cookie or CORS setup. Paths the LangGraph server owns are left to
it: the custom app's routes are matched ahead of the server's, so the catch-all
declines them instead of shadowing them. Paths are taken relative to the mount,
so a LangGraph ``http.mount_prefix`` serves the UI under that prefix as long as
the build was made for it (``DASHBOARD_BASE_PATH``).

For UI development, ``DASHBOARD_DEV_SERVER_URL`` swaps the build for a reverse
proxy to the Vite dev server, so the same origin hot-reloads. Vite's HMR
WebSocket is not proxied: the UI's Vite config points the client at Vite's own
port.
"""

import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Match, Route, get_route_path
from starlette.types import Scope

from agent.config import ENV

logger = logging.getLogger(__name__)

SHELL_FILE = "_shell.html"
ASSETS_DIR = "assets"
_REPO_BUILD_DIR = Path(__file__).resolve().parents[2] / "ui" / ".output" / "public"

# Owned by the LangGraph server or this API; never a UI route.
RESERVED_PREFIXES: tuple[str, ...] = (
    "/dashboard/api",
    "/webhooks",
    "/health",
    "/assistants",
    "/threads",
    "/runs",
    "/store",
    "/mcp",
    "/a2a",
    "/ui",
    "/docs",
    "/openapi.json",
    "/info",
    "/metrics",
    "/ok",
    f"/{ASSETS_DIR}",
)


def dashboard_static_dir() -> Path | None:
    """Directory holding a dashboard build, or None when there is none to serve.

    ``DASHBOARD_STATIC_DIR`` names it explicitly, and a named directory without a
    build means no UI (so images and tests behave the same everywhere); otherwise
    the in-repo build from ``make build-dashboard`` is served when present.
    """
    configured = ENV.DASHBOARD_STATIC_DIR.optional()
    candidate = Path(configured) if configured else _REPO_BUILD_DIR
    if (candidate / SHELL_FILE).is_file():
        return candidate.resolve()
    return None


def is_reserved_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in RESERVED_PREFIXES)


def _accepts_html(scope: Scope) -> bool:
    for name, value in scope.get("headers", ()):
        if name == b"accept":
            accept = value.decode("latin-1").lower()
            return "text/html" in accept or "application/xhtml+xml" in accept
    return False


class ImmutableStaticFiles(StaticFiles):
    """Hashed build assets never change under the same name."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class DashboardCatchAll(Route):
    """A ``/{path:path}`` route that leaves the LangGraph server's paths alone.

    ``matches`` declines reserved paths so Starlette keeps looking and the
    server's routes (matched after the custom app's) still answer.
    """

    def __init__(
        self, endpoint: Callable[[Request], Awaitable[Response]], methods: list[str]
    ) -> None:
        super().__init__(
            "/{path:path}",
            endpoint=endpoint,
            methods=methods,
            include_in_schema=False,
            name="dashboard-ui",
        )

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] != "http" or is_reserved_path(get_route_path(scope)):
            return Match.NONE, {}
        return super().matches(scope)


class DashboardShellRoute(DashboardCatchAll):
    """Catch-all for the UI: files from the build, the shell for navigations.

    Unknown paths are declined unless the request accepts HTML, so an API client
    hitting a wrong URL gets the server's 404 rather than the shell.
    """

    def __init__(self, static_dir: Path) -> None:
        self.static_dir = static_dir
        super().__init__(self._serve, ["GET", "HEAD"])

    def file_for(self, path: str) -> Path | None:
        relative = path.lstrip("/")
        if not relative:
            return None
        candidate = (self.static_dir / relative).resolve()
        if candidate.is_relative_to(self.static_dir) and candidate.is_file():
            return candidate
        return None

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] == "http" and self.file_for(get_route_path(scope)) is None:
            if not _accepts_html(scope):
                return Match.NONE, {}
        return super().matches(scope)

    async def _serve(self, request: Request) -> Response:
        file = self.file_for(get_route_path(request.scope))
        if file is not None:
            return FileResponse(file)
        # The shell is the entry point for every UI route, so browsers must
        # revalidate it to pick up a new build's asset hashes.
        return FileResponse(self.static_dir / SHELL_FILE, headers={"Cache-Control": "no-cache"})


# Not forwarded in either direction (RFC 9110 section 7.6.1).
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Uvicorn adds its own; forwarding Vite's would duplicate them.
_SERVER_HEADERS = frozenset({"date", "server"})
_PROXIED_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


class DashboardDevProxyRoute(DashboardCatchAll):
    """Catch-all for UI development: every non-reserved request goes to Vite.

    The browser stays on the backend's origin, so cookies, the login callback and
    the API work exactly as with a bundled build, while Vite serves the modules
    and hot-reloads them. Bodies stream both ways, the upstream ``Host`` is
    Vite's own (it checks it against ``server.allowedHosts``), and redirects are
    passed back rather than followed.
    """

    def __init__(self, upstream: str, client: httpx.AsyncClient | None = None) -> None:
        self.upstream = upstream.rstrip("/")
        self.client = client or httpx.AsyncClient(
            base_url=self.upstream,
            # Vite compiles a route on its first request; that can take a while.
            timeout=httpx.Timeout(120.0, connect=5.0),
            follow_redirects=False,
        )
        super().__init__(self._proxy, _PROXIED_METHODS)

    async def _proxy(self, request: Request) -> Response:
        path = get_route_path(request.scope) or "/"
        target = f"{path}?{request.url.query}" if request.url.query else path
        headers = [
            (key, value)
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS and key.lower() != "host"
        ]
        body = None if request.method in ("GET", "HEAD", "OPTIONS") else request.stream()
        try:
            upstream_request = self.client.build_request(
                request.method, target, headers=headers, content=body
            )
            upstream = await self.client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            return PlainTextResponse(
                f"The dashboard dev server at {self.upstream} did not answer ({exc}). "
                "Start it with `make web`, or unset DASHBOARD_DEV_SERVER_URL to serve a build.",
                status_code=502,
            )
        response = StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            background=BackgroundTask(upstream.aclose),
        )
        response.raw_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in upstream.headers.multi_items()
            if key.lower() not in _HOP_BY_HOP_HEADERS and key.lower() not in _SERVER_HEADERS
        ]
        return response


def mount_dashboard_ui(app: FastAPI) -> Path | None:
    """Serve the dashboard at ``/``: a build when one exists, or the Vite dev
    server named by ``DASHBOARD_DEV_SERVER_URL``. Returns the build directory.

    Register after every API router: the catch-all must come last. Code that adds
    routes to the app afterwards calls ``keep_dashboard_ui_last`` when done. The
    route must not look at the live route table instead: the LangGraph server
    rewrites the app's routes to append its own catch-all after this one.
    """
    dev_server = ENV.DASHBOARD_DEV_SERVER_URL.optional()
    if dev_server:
        app.router.routes.append(DashboardDevProxyRoute(dev_server))
        # httpx logs every request at INFO; that is one line per module Vite serves.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logger.info("Serving the dashboard from the Vite dev server at %s", dev_server)
        return None
    static_dir = dashboard_static_dir()
    if static_dir is None:
        return None
    assets = static_dir / ASSETS_DIR
    if assets.is_dir():
        app.mount(f"/{ASSETS_DIR}", ImmutableStaticFiles(directory=assets), name="dashboard-assets")
    app.router.routes.append(DashboardShellRoute(static_dir))
    logger.info("Serving the dashboard from %s", static_dir)
    return static_dir


def keep_dashboard_ui_last(app: FastAPI) -> None:
    """Move the UI catch-all behind routes registered since ``mount_dashboard_ui``."""
    routes = app.router.routes
    for index, route in enumerate(routes):
        if isinstance(route, DashboardCatchAll):
            routes.append(routes.pop(index))
            return
