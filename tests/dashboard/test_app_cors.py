"""CORS allowlist derivation for the FastAPI app."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.api import app as app_module


def _cors_origins(app: FastAPI) -> list[str] | None:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            origins = middleware.kwargs["allow_origins"]
            assert isinstance(origins, list)
            return sorted(str(origin) for origin in origins)
    return None


def test_cors_uses_dashboard_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example/")
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)

    assert _cors_origins(app_module.create_app()) == ["https://dashboard.example"]


def test_cors_adds_extra_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv(
        "DASHBOARD_ALLOWED_ORIGINS", "https://preview.example, http://localhost:3000"
    )

    assert _cors_origins(app_module.create_app()) == [
        "http://localhost:3000",
        "https://preview.example",
    ]


def test_cors_extra_origins_alone_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    assert _cors_origins(app_module.create_app()) == ["https://preview.example"]


def test_cors_disabled_without_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)

    assert _cors_origins(app_module.create_app()) is None


def test_cors_rejects_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError):
        app_module.create_app()
