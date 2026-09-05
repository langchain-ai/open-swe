"""Serving the bundled dashboard from the backend's own origin."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

from agent.api import app as app_module
from agent.utils import startup_config
from agent.utils.dashboard_ui import DashboardShellRoute, dashboard_static_dir

HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
SHELL = "<!doctype html><div id=root>shell</div>"


@pytest.fixture
def build_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "_shell.html").write_text(SHELL)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "favicon.png").write_bytes(b"\x89PNG")
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    return tmp_path


def _shell_route(app) -> DashboardShellRoute:
    return next(route for route in app.router.routes if isinstance(route, DashboardShellRoute))


def _scope(path: str, accept: str | None = "text/html") -> dict:
    headers = [(b"accept", accept.encode())] if accept else []
    return {"type": "http", "method": "GET", "path": path, "headers": headers}


def test_shell_answers_browser_navigations(build_dir: Path) -> None:
    client = TestClient(app_module.create_app())

    for path in ("/", "/agents/thread-1", "/admin/team?tab=repos"):
        response = client.get(path, headers=HTML)
        assert response.status_code == 200, path
        assert response.text == SHELL
        assert response.headers["cache-control"] == "no-cache"


def test_build_files_are_served_as_themselves(build_dir: Path) -> None:
    client = TestClient(app_module.create_app())

    favicon = client.get("/favicon.png")
    assert favicon.status_code == 200
    assert favicon.content == b"\x89PNG"

    asset = client.get("/assets/app-abc123.js")
    assert asset.status_code == 200
    assert asset.text == "console.log(1)"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize(
    "path",
    [
        "/threads",
        "/threads/abc/runs",
        "/runs/stream",
        "/assistants/search",
        "/store/items",
        "/docs",
        "/openapi.json",
        "/ok",
        "/info",
        "/metrics",
        "/ui/agent",
        "/mcp",
        "/dashboard/api/session",
        "/webhooks/github",
        "/health",
    ],
)
def test_reserved_paths_are_left_to_the_langgraph_server(build_dir: Path, path: str) -> None:
    """The custom app's routes are matched first, so the catch-all must decline these."""
    route = _shell_route(app_module.create_app())

    assert route.matches(_scope(path))[0] is Match.NONE


def test_root_without_html_accept_is_left_to_the_health_check(build_dir: Path) -> None:
    route = _shell_route(app_module.create_app())

    assert route.matches(_scope("/", accept=None))[0] is Match.NONE
    assert route.matches(_scope("/", accept="*/*"))[0] is Match.NONE
    assert route.matches(_scope("/", accept="text/html"))[0] is Match.FULL


def test_api_routes_keep_precedence_over_the_shell(build_dir: Path) -> None:
    client = TestClient(app_module.create_app())

    response = client.get("/health", headers=HTML)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_unknown_paths_without_html_accept_fall_through(build_dir: Path) -> None:
    client = TestClient(app_module.create_app())

    assert client.get("/agents/x", headers={"Accept": "application/json"}).status_code == 404
    assert client.post("/agents/x", headers=HTML).status_code in (404, 405)


def test_files_outside_the_build_are_never_served(build_dir: Path) -> None:
    outside = build_dir.parent / f"{build_dir.name}-outside.txt"
    outside.write_text("secret")
    route = _shell_route(app_module.create_app())

    assert route.file_for(f"/../{outside.name}") is None
    assert route.file_for("/_shell.html") == build_dir / "_shell.html"
    assert route.file_for("/") is None


def test_no_build_means_no_ui(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path / "missing"))
    app = app_module.create_app()

    assert dashboard_static_dir() is None
    assert not any(isinstance(route, DashboardShellRoute) for route in app.router.routes)
    assert TestClient(app).get("/", headers=HTML).status_code == 404
    assert startup_config.dashboard_ui_summary() == "Dashboard UI: not bundled"


def test_startup_summary_names_the_build(build_dir: Path) -> None:
    assert (
        startup_config.dashboard_ui_summary() == f"Dashboard UI: bundled from {build_dir.resolve()}"
    )


def test_serving_under_a_mount_prefix(build_dir: Path) -> None:
    """``http.mount_prefix`` wraps the whole app in a Mount; paths are read relative to it."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    client = TestClient(Starlette(routes=[Mount("/open-swe", app=app_module.create_app())]))

    assert client.get("/open-swe/", headers=HTML).text == SHELL
    assert client.get("/open-swe/agents/t1", headers=HTML).text == SHELL
    assert client.get("/open-swe/favicon.png").content == b"\x89PNG"
    assert client.get("/open-swe/assets/app-abc123.js").status_code == 200
    assert client.get("/open-swe/threads", headers=HTML).status_code == 404
    assert client.get("/", headers=HTML).status_code == 404
