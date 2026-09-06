"""Serve the built dashboard from the backend's own origin.

A dashboard build (``ui/.output/public``: a client-only ``_shell.html`` plus
hashed assets) mounted at ``/`` lets one LangGraph deployment serve both the API
and the UI, so the browser reaches ``/dashboard/api/*`` with relative URLs and no
cross-origin cookie or CORS setup. Paths the LangGraph server owns are left to
it: the custom app's routes are matched ahead of the server's, so the catch-all
declines them instead of shadowing them. Paths are taken relative to the mount,
so a LangGraph ``http.mount_prefix`` serves the UI under that prefix as long as
the build was made for it (``DASHBOARD_BASE_PATH``).
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import FileResponse, Response
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


class DashboardShellRoute(Route):
    """Catch-all for the UI: files from the build, the shell for navigations.

    ``matches`` declines reserved paths, and unknown paths unless the request
    accepts HTML, so Starlette keeps looking and the LangGraph server's routes
    (matched after the custom app's) still answer.
    """

    def __init__(self, static_dir: Path) -> None:
        self.static_dir = static_dir
        super().__init__(
            "/{path:path}",
            endpoint=self._serve,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name="dashboard-ui",
        )

    def file_for(self, path: str) -> Path | None:
        relative = path.lstrip("/")
        if not relative:
            return None
        candidate = (self.static_dir / relative).resolve()
        if candidate.is_relative_to(self.static_dir) and candidate.is_file():
            return candidate
        return None

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] != "http":
            return Match.NONE, {}
        path = get_route_path(scope)
        if is_reserved_path(path):
            return Match.NONE, {}
        if self.file_for(path) is None and not _accepts_html(scope):
            return Match.NONE, {}
        return super().matches(scope)

    async def _serve(self, request: Request) -> Response:
        file = self.file_for(get_route_path(request.scope))
        if file is not None:
            return FileResponse(file)
        # The shell is the entry point for every UI route, so browsers must
        # revalidate it to pick up a new build's asset hashes.
        return FileResponse(self.static_dir / SHELL_FILE, headers={"Cache-Control": "no-cache"})


def mount_dashboard_ui(app: FastAPI) -> Path | None:
    """Serve the dashboard build at ``/`` when one exists; returns its directory.

    Register after every API router: the catch-all must come last. Code that adds
    routes to the app afterwards calls ``keep_dashboard_ui_last`` when done. The
    route must not look at the live route table instead: the LangGraph server
    rewrites the app's routes to append its own catch-all after this one.
    """
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
        if isinstance(route, DashboardShellRoute):
            routes.append(routes.pop(index))
            return
