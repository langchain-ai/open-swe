"""Serving the bundled dashboard from the backend's own origin."""

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from openswe.api import app as app_module
from openswe.utils.dashboard_ui import (
    DashboardDevProxyRoute,
    DashboardShellRoute,
    keep_dashboard_ui_last,
)

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


def test_desktop_api_preflight_without_extra_cors_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
    client = TestClient(app_module.create_app())

    response = client.options(
        "/dashboard/api/me",
        headers={
            "Origin": "open-swe://app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "open-swe://app"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert (
        client.options(
            "/dashboard/api/me",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        ).status_code
        == 400
    )


def test_api_routes_keep_precedence_over_the_shell(build_dir: Path) -> None:
    client = TestClient(app_module.create_app())

    response = client.get("/health", headers=HTML)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")


def test_routes_added_after_the_ui_win_once_it_is_moved_last(build_dir: Path) -> None:
    """The e2e harness composes its fake-SaaS pages onto the built app."""
    from fastapi.responses import HTMLResponse

    app = app_module.create_app()

    @app.get("/mock/slack", response_class=HTMLResponse)
    async def mock_slack() -> str:
        return "<button id=reset>reset</button>"

    assert TestClient(app).get("/mock/slack", headers=HTML).text == SHELL

    keep_dashboard_ui_last(app)
    client = TestClient(app)

    assert client.get("/mock/slack", headers=HTML).text == "<button id=reset>reset</button>"
    assert client.get("/agents/t1", headers=HTML).text == SHELL
    assert isinstance(app.router.routes[-1], DashboardShellRoute)


def test_files_outside_the_build_are_never_served(build_dir: Path) -> None:
    outside = build_dir.parent / f"{build_dir.name}-outside.txt"
    outside.write_text("secret")
    route = _shell_route(app_module.create_app())

    assert route.file_for(f"/../{outside.name}") is None
    assert route.file_for("/_shell.html") == build_dir / "_shell.html"
    assert route.file_for("/") is None


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


# --- Vite dev server proxy -------------------------------------------------------------


def _vite_response(
    status: int, body: bytes, headers: list[tuple[str, str]] | None = None
) -> httpx2.Response:
    """What a real Vite sends: a response whose body is still to be streamed."""
    return httpx2.Response(status, stream=httpx2.ByteStream(body), headers=headers)


def _vite_app(handler, monkeypatch: pytest.MonkeyPatch):
    """The app with the UI route forwarding to a fake Vite, answered by ``handler``."""
    monkeypatch.setenv("DASHBOARD_DEV_SERVER_URL", "http://vite.test:3000/")
    app = app_module.create_app()
    route = next(r for r in app.router.routes if isinstance(r, DashboardDevProxyRoute))
    route.client = httpx2.AsyncClient(
        base_url="http://vite.test:3000",
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
    return app


def test_dev_proxy_forwards_modules_and_headers_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    def vite(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/src/routes/index.tsx"
        return _vite_response(
            200,
            b"export default 1",
            [
                ("content-type", "text/javascript"),
                ("etag", 'W/"abc"'),
                ("set-cookie", "a=1; Path=/"),
                ("set-cookie", "b=2; Path=/"),
                ("connection", "keep-alive"),
                ("date", "Mon, 01 Jan 2024 00:00:00 GMT"),
            ],
        )

    client = TestClient(_vite_app(vite, monkeypatch))
    response = client.get("/src/routes/index.tsx", headers={"Accept": "*/*"})

    assert response.status_code == 200
    assert response.content == b"export default 1"
    assert response.headers["etag"] == 'W/"abc"'
    assert response.headers.get_list("set-cookie") == ["a=1; Path=/", "b=2; Path=/"]
    assert "connection" not in response.headers
    assert response.headers.get_list("date") != ["Mon, 01 Jan 2024 00:00:00 GMT"]


def test_dev_proxy_passes_redirects_and_bodies_through(monkeypatch: pytest.MonkeyPatch) -> None:
    def vite(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            assert request.read() == b'{"x":1}'
            return _vite_response(201, b'{"ok": true}', [("content-type", "application/json")])
        return _vite_response(302, b"", [("location", "/login?next=%2Fadmin")])

    client = TestClient(_vite_app(vite, monkeypatch))
    redirect = client.get("/admin", headers=HTML, follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "/login?next=%2Fadmin"

    created = client.post(
        "/_serverFn/x", content=b'{"x":1}', headers={"content-type": "application/json"}
    )
    assert created.status_code == 201
    assert created.json() == {"ok": True}
