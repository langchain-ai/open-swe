from __future__ import annotations

from agent.dashboard.oauth import sanitize_redirect_to


def test_sanitize_redirect_to_preserves_allowed_dashboard_target(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    target = "https://dashboard.example/agents/thread-1/plan?from=slack#review"

    assert sanitize_redirect_to(target) == target


def test_sanitize_redirect_to_preserves_allowed_preview_target(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    target = "https://preview.example/agents/thread-1/plan?from=slack#review"

    assert sanitize_redirect_to(target) == target


def test_sanitize_redirect_to_rejects_external_target(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    assert sanitize_redirect_to("https://evil.example/agents/thread-1/plan") == (
        "https://dashboard.example"
    )
