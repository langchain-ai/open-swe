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


def test_account_link_prompt_posts_generic_token_free_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt posts a plain settings link in the thread — no per-user token."""
    import asyncio

    from agent import webapp

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    calls: dict[str, object] = {}

    async def fake_reply(channel_id, thread_ts, text):
        calls["reply"] = {"channel_id": channel_id, "thread_ts": thread_ts, "text": text}
        return True

    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_reply)

    asyncio.run(webapp._post_account_link_prompt("C1", "1.1", "U1", "d@x.com", reason="unlinked"))
    assert calls["reply"]["channel_id"] == "C1"
    assert calls["reply"]["thread_ts"] == "1.1"
    assert "https://app.example.com/my-settings" in calls["reply"]["text"]
    # No signed account-link token may appear in the public thread.
    assert "link=" not in calls["reply"]["text"]


def test_account_link_prompt_revoked_wording(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from agent import webapp

    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://app.example.com")
    calls: dict[str, object] = {}

    async def fake_reply(channel_id, thread_ts, text):
        calls["text"] = text
        return True

    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_reply)

    asyncio.run(webapp._post_account_link_prompt("C1", "1.1", "U1", "d@x.com", reason="revoked"))
    assert "no longer valid" in calls["text"]
    assert "link=" not in calls["text"]


def test_account_link_prompt_skips_when_dashboard_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from agent import webapp

    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    posted = False

    async def fake_reply(channel_id, thread_ts, text):
        nonlocal posted
        posted = True
        return True

    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_reply)

    asyncio.run(webapp._post_account_link_prompt("C1", "1.1", "U1", "d@x.com", reason="unlinked"))
    assert posted is False
