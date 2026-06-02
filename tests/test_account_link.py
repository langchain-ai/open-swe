"""Tests for the Slack→GitHub account-link OAuth threading."""

from __future__ import annotations

import pytest

from agent.dashboard import oauth


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret")


def test_account_link_round_trip() -> None:
    token = oauth.issue_account_link(slack_user_id="U123", work_email="dev@x.com")
    payload = oauth.decode_account_link(token)
    assert payload is not None
    assert payload["slack_user_id"] == "U123"
    assert payload["work_email"] == "dev@x.com"
    assert payload["kind"] == "account_link"


def test_decode_account_link_rejects_garbage() -> None:
    assert oauth.decode_account_link("") is None
    assert oauth.decode_account_link("not-a-jwt") is None


def test_decode_account_link_rejects_wrong_kind() -> None:
    # A session token is a valid JWT but not an account-link token.
    session = oauth.issue_session(login="x", email="x@x.com", avatar_url=None)
    assert oauth.decode_account_link(session) is None


def test_build_account_link_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example.com/")
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    url = oauth.build_account_link_url(slack_user_id="U1", work_email="d@x.com")
    assert url is not None
    assert url.startswith("https://api.example.com/dashboard/api/auth/login?link=")
    # The embedded token must decode back to the same identity.
    token = url.split("link=", 1)[1].split("&", 1)[0]
    from urllib.parse import unquote

    payload = oauth.decode_account_link(unquote(token))
    assert payload["slack_user_id"] == "U1"


def test_build_account_link_url_redirects_to_profile_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    url = oauth.build_account_link_url(slack_user_id="U1", work_email="d@x.com")
    assert url is not None
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(url).query)
    assert query["redirect_to"] == ["https://app.example.com/my-settings"]


def test_build_account_link_url_none_without_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_API_BASE_URL", raising=False)
    assert oauth.build_account_link_url(slack_user_id="U1", work_email="d@x.com") is None
