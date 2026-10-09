"""Web base URLs and the session cookie policy that follows from them."""

from pathlib import Path

import pytest

from openswe.utils import web_links
from openswe.web import oauth as web_oauth


@pytest.fixture
def bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "_shell.html").write_text("<!doctype html>")
    monkeypatch.setenv("WEB_STATIC_DIR", str(tmp_path))
    return tmp_path


def test_same_origin_https_session_cookie_is_lax(
    monkeypatch: pytest.MonkeyPatch, bundled: Path
) -> None:
    monkeypatch.delenv("WEB_BASE_URL", raising=False)
    monkeypatch.delenv("WEB_API_BASE_URL", raising=False)
    monkeypatch.setenv("LANGGRAPH_URL", "https://backend.example")

    assert web_links.web_is_same_origin() is True
    assert web_oauth.cookie_security() == (True, "lax")


def test_cross_origin_https_session_cookie_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_BASE_URL", "https://web.example")
    monkeypatch.setenv("WEB_API_BASE_URL", "https://api.example")

    assert web_links.web_is_same_origin() is False
    assert web_oauth.cookie_security() == (True, "none")


def test_local_dev_model_check_needs_an_explicit_localhost_web(
    monkeypatch: pytest.MonkeyPatch, bundled: Path
) -> None:
    """A fresh platform deployment has the bundled UI and no LANGGRAPH_URL yet; it must boot."""
    from openswe.utils import model

    for name in ("WEB_BASE_URL", "LANGGRAPH_URL", "OPENAI_API_KEY", "LLM_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(model, "desktop_openai_oauth_available", lambda: False)

    assert web_links.web_base_url() == "http://localhost:2024"
    model.validate_local_dev_llm_config()

    monkeypatch.setenv("WEB_BASE_URL", "http://localhost:3000")
    with pytest.raises(ValueError, match="_API_KEY is required"):
        model.validate_local_dev_llm_config()
