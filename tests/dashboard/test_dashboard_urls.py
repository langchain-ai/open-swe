"""Dashboard base URLs and the session cookie policy that follows from them."""

from pathlib import Path

import pytest

from agent.dashboard import oauth as dashboard_oauth
from agent.utils import dashboard_links


@pytest.fixture
def bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "_shell.html").write_text("<!doctype html>")
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path))
    return tmp_path


def test_same_origin_https_session_cookie_is_lax(
    monkeypatch: pytest.MonkeyPatch, bundled: Path
) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_API_BASE_URL", raising=False)
    monkeypatch.setenv("LANGGRAPH_URL", "https://backend.example")

    assert dashboard_links.dashboard_is_same_origin() is True
    assert dashboard_oauth.cookie_security() == (True, "lax")


def test_cross_origin_https_session_cookie_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example")

    assert dashboard_links.dashboard_is_same_origin() is False
    assert dashboard_oauth.cookie_security() == (True, "none")


def test_local_dev_model_check_needs_an_explicit_localhost_dashboard(
    monkeypatch: pytest.MonkeyPatch, bundled: Path
) -> None:
    """A fresh platform deployment has the bundled UI and no LANGGRAPH_URL yet; it must boot."""
    from agent.utils import model

    for name in ("DASHBOARD_BASE_URL", "LANGGRAPH_URL", "OPENAI_API_KEY", "LLM_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)

    assert dashboard_links.dashboard_base_url() == "http://localhost:2024"
    model.validate_local_dev_llm_config()

    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:3000")
    with pytest.raises(ValueError, match="_API_KEY is required"):
        model.validate_local_dev_llm_config()
