from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from openswe.slack import oauth as slack_oauth


def test_build_authorize_url_includes_team_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_oauth, "SLACK_CLIENT_ID", "cid")
    monkeypatch.setattr(slack_oauth, "SLACK_TEAM_ID", "T123")
    url = slack_oauth.build_authorize_url(redirect_uri="https://x/cb", state="S")
    assert parse_qs(urlparse(url).query)["team"] == ["T123"]


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
