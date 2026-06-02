"""Sign in with Slack (OpenID Connect) for self-service GitHub ⇄ Slack linking.

Reuses the existing Slack app — Sign in with Slack is a capability on the same
app that owns the bot token, so it only needs the ``openid email profile`` user
scopes, a redirect URL, and the app's client id/secret. The id_token/userInfo
claims give us a Slack-*verified* member id and email, so a logged-in GitHub
user can only ever link their own Slack identity (no self-asserted spoofing).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET", "")
# Optional: restrict linking to a single workspace (the Slack team id, T...).
SLACK_TEAM_ID = os.environ.get("SLACK_TEAM_ID", "")

SLACK_STATE_COOKIE_NAME = "osw_slack_oauth_state"
SLACK_OIDC_SCOPES = "openid email profile"

_AUTHORIZE_URL = "https://slack.com/openid/connect/authorize"
_TOKEN_URL = "https://slack.com/api/openid.connect.token"
_USERINFO_URL = "https://slack.com/api/openid.connect.userInfo"
_USER_ID_CLAIM = "https://slack.com/user_id"
_TEAM_ID_CLAIM = "https://slack.com/team_id"


def slack_oauth_configured() -> bool:
    return bool(SLACK_CLIENT_ID and SLACK_CLIENT_SECRET)


@dataclass(frozen=True)
class SlackIdentity:
    user_id: str
    team_id: str
    email: str | None
    email_verified: bool
    name: str | None


def build_authorize_url(*, redirect_uri: str, state: str) -> str:
    params = {
        "response_type": "code",
        "scope": SLACK_OIDC_SCOPES,
        "client_id": SLACK_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if SLACK_TEAM_ID:
        # Pre-selects the workspace so users can't accidentally sign in elsewhere.
        params["team"] = SLACK_TEAM_ID
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def parse_slack_identity(data: dict[str, Any]) -> SlackIdentity:
    """Build a SlackIdentity from an openid.connect.userInfo response."""
    if not data.get("ok", True):
        raise HTTPException(400, f"slack userinfo failed: {data.get('error', 'unknown')}")
    user_id = data.get(_USER_ID_CLAIM)
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(400, "slack userinfo missing user id")
    team_id = data.get(_TEAM_ID_CLAIM)
    email = data.get("email")
    return SlackIdentity(
        user_id=user_id,
        team_id=team_id if isinstance(team_id, str) else "",
        email=email if isinstance(email, str) and email else None,
        email_verified=bool(data.get("email_verified")),
        name=data.get("name") if isinstance(data.get("name"), str) else None,
    )


def verify_team(identity: SlackIdentity) -> None:
    """Reject identities from a different workspace when one is configured."""
    if SLACK_TEAM_ID and identity.team_id != SLACK_TEAM_ID:
        raise HTTPException(403, "Slack account is not in the authorized workspace")


async def exchange_slack_code(code: str, redirect_uri: str) -> str:
    """Exchange an authorization code for a user access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "client_id": SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok") or not data.get("access_token"):
        raise HTTPException(400, f"slack oauth exchange failed: {data.get('error', 'unknown')}")
    return data["access_token"]


async def fetch_slack_identity(access_token: str) -> SlackIdentity:
    """Resolve the signed-in Slack user's verified identity."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    resp.raise_for_status()
    return parse_slack_identity(resp.json())
