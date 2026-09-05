"""Dashboard base URLs and the session cookie policy that follows from them."""

from pathlib import Path

import pytest

from agent.dashboard import routes as dashboard_routes
from agent.utils import dashboard_links


@pytest.fixture
def bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "_shell.html").write_text("<!doctype html>")
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    return tmp_path


def test_explicit_dashboard_base_url_wins(monkeypatch: pytest.MonkeyPatch, bundled: Path) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example/")
    monkeypatch.setenv("LANGGRAPH_URL", "https://backend.example")

    assert dashboard_links.dashboard_base_url() == "https://dashboard.example"


def test_bundled_dashboard_lives_on_the_backend_origin(
    monkeypatch: pytest.MonkeyPatch, bundled: Path
) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.setenv("LANGGRAPH_URL", "https://backend.example/")

    assert dashboard_links.dashboard_base_url() == "https://backend.example"
    assert dashboard_links.dashboard_thread_url("t1") == "https://backend.example/agents/t1"


def test_no_dashboard_means_no_links(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)

    assert dashboard_links.dashboard_base_url() == ""
    assert dashboard_links.dashboard_thread_url("t1") is None
    assert dashboard_links.dashboard_review_url("o", "r", 1) is None


def test_api_base_url_defaults_to_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_API_BASE_URL", raising=False)
    monkeypatch.setenv("LANGGRAPH_URL", "https://backend.example/")

    assert dashboard_links.dashboard_api_base_url() == "https://backend.example"

    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example/")
    assert dashboard_links.dashboard_api_base_url() == "https://api.example"


def test_same_origin_https_session_cookie_is_lax(
    monkeypatch: pytest.MonkeyPatch, bundled: Path
) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_API_BASE_URL", raising=False)
    monkeypatch.setenv("LANGGRAPH_URL", "https://backend.example")

    assert dashboard_links.dashboard_is_same_origin() is True
    assert dashboard_routes._cookie_security() == (True, "lax")


def test_cross_origin_https_session_cookie_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example")

    assert dashboard_links.dashboard_is_same_origin() is False
    assert dashboard_routes._cookie_security() == (True, "none")


def test_local_http_session_cookie_is_lax_and_not_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://localhost:2024")

    assert dashboard_routes._cookie_security() == (False, "lax")
