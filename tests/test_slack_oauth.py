from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from agent.dashboard import slack_oauth


def test_slack_oauth_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_ID", "cid")
    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_SECRET", "secret")
    assert slack_oauth.slack_oauth_configured() is True
    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_SECRET", "")
    assert slack_oauth.slack_oauth_configured() is False


def test_build_authorize_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_ID", "cid")
    monkeypatch.setattr(slack_oauth, "SLACK_TEAM_ID", "")
    url = slack_oauth.build_authorize_url(
        redirect_uri="http://localhost:2024/dashboard/api/slack/callback", state="ST8"
    )
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.netloc == "slack.com"
    assert q["response_type"] == ["code"]
    assert q["scope"] == ["openid email profile"]
    assert q["client_id"] == ["cid"]
    assert q["redirect_uri"] == ["http://localhost:2024/dashboard/api/slack/callback"]
    assert q["state"] == ["ST8"]
    assert "team" not in q


def test_build_authorize_url_includes_team_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_ID", "cid")
    monkeypatch.setattr(slack_oauth, "SLACK_TEAM_ID", "T123")
    url = slack_oauth.build_authorize_url(redirect_uri="https://x/cb", state="S")
    assert parse_qs(urlparse(url).query)["team"] == ["T123"]


def test_parse_slack_identity_success() -> None:
    identity = slack_oauth.parse_slack_identity(
        {
            "ok": True,
            "https://slack.com/user_id": "U999",
            "https://slack.com/team_id": "T123",
            "email": "dev@example.com",
            "email_verified": True,
            "name": "Dev",
        }
    )
    assert identity.user_id == "U999"
    assert identity.team_id == "T123"
    assert identity.email == "dev@example.com"
    assert identity.email_verified is True
    assert identity.name == "Dev"


def test_parse_slack_identity_missing_user_id() -> None:
    with pytest.raises(HTTPException):
        slack_oauth.parse_slack_identity({"ok": True, "email": "x@y.com"})


def test_parse_slack_identity_not_ok() -> None:
    with pytest.raises(HTTPException):
        slack_oauth.parse_slack_identity({"ok": False, "error": "bad"})


def test_verify_team(monkeypatch: pytest.MonkeyPatch) -> None:
    ident = slack_oauth.SlackIdentity(
        user_id="U1", team_id="T1", email="a@b.com", email_verified=True, name=None
    )
    # No workspace restriction configured → always allowed.
    monkeypatch.setattr(slack_oauth, "SLACK_TEAM_ID", "")
    slack_oauth.verify_team(ident)
    # Matching workspace → allowed.
    monkeypatch.setattr(slack_oauth, "SLACK_TEAM_ID", "T1")
    slack_oauth.verify_team(ident)
    # Different workspace → rejected.
    monkeypatch.setattr(slack_oauth, "SLACK_TEAM_ID", "T2")
    with pytest.raises(HTTPException):
        slack_oauth.verify_team(ident)
