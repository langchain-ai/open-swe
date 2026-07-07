from __future__ import annotations

import importlib

dispatch = importlib.import_module("agent.dispatch")

_ABSOLUTE = "https://open-swe-v3-abc.us.langgraph.app/webhooks/run-complete"


def test_is_loopback_webhook_relative() -> None:
    assert dispatch._is_loopback_webhook("/webhooks/run-complete") is True


def test_is_loopback_webhook_localhost() -> None:
    assert dispatch._is_loopback_webhook("http://localhost:2024/webhooks/run-complete") is True
    assert dispatch._is_loopback_webhook("http://127.0.0.1:8000/webhooks/run-complete") is True


def test_is_loopback_webhook_absolute() -> None:
    assert dispatch._is_loopback_webhook(_ABSOLUTE) is False


def test_resolve_no_secret_attaches_nothing() -> None:
    assert dispatch._resolve_completion_webhook_url(_ABSOLUTE, None) is None
    assert dispatch._resolve_completion_webhook_url(_ABSOLUTE, "") is None


def test_resolve_relative_url_degrades_to_none() -> None:
    # Secret set but a loopback URL would 422 every run — attach nothing instead.
    assert dispatch._resolve_completion_webhook_url("/webhooks/run-complete", "s3cret") is None


def test_resolve_localhost_url_degrades_to_none() -> None:
    assert dispatch._resolve_completion_webhook_url("http://localhost/x", "s3cret") is None


def test_resolve_absolute_url_appends_token() -> None:
    assert (
        dispatch._resolve_completion_webhook_url(_ABSOLUTE, "s3cret") == f"{_ABSOLUTE}?token=s3cret"
    )


def test_resolve_absolute_url_with_existing_query_left_as_is() -> None:
    url = f"{_ABSOLUTE}?token=preset"
    assert dispatch._resolve_completion_webhook_url(url, "s3cret") == url
