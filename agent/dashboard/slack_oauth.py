"""Slack user OAuth (Sign in with Slack) for account-linking verification.

Uses Slack's OpenID Connect endpoints. The ``openid email profile`` scopes
return the workspace-scoped user_id and team_id we need to key the account
link, plus the verified email. The access token is discarded after the
``openid.connect.userinfo`` call — bot-side calls keep using ``SLACK_BOT_TOKEN``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

SLACK_AUTHORIZE_URL = "https://slack.com/openid/connect/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/openid.connect.token"
SLACK_USERINFO_URL = "https://slack.com/api/openid.connect.userinfo"
SLACK_USER_SCOPES = "openid,email,profile"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    return (
        f"{SLACK_AUTHORIZE_URL}?response_type=code"
        f"&scope={SLACK_USER_SCOPES}"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )


async def exchange_code(*, code: str, redirect_uri: str) -> str:
    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(500, "Slack OAuth client credentials not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            SLACK_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if response.status_code != 200:
        logger.warning("Slack token exchange failed: %s %s", response.status_code, response.text)
        raise HTTPException(400, f"Slack token exchange failed: {response.status_code}")
    data = response.json()
    if not data.get("ok", False):
        logger.warning("Slack token exchange ok=false: %s", data)
        raise HTTPException(400, f"Slack token exchange ok=false: {data.get('error')}")
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(400, "Slack token response missing access_token")
    return token


async def fetch_userinfo(access_token: str) -> dict[str, str]:
    """Return verified Slack identity for the authorized user.

    The OIDC userinfo response uses prefixed keys like
    ``https://slack.com/user_id`` to disambiguate from generic OIDC claims.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            SLACK_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code != 200:
        raise HTTPException(400, f"Slack userinfo failed: {response.status_code}")
    payload: dict[str, Any] = response.json()
    if not payload.get("ok", False):
        raise HTTPException(400, f"Slack userinfo ok=false: {payload.get('error')}")
    user_id = payload.get("https://slack.com/user_id") or payload.get("sub")
    team_id = payload.get("https://slack.com/team_id") or ""
    email = payload.get("email") or ""
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(400, "Slack userinfo response missing user_id")
    if not isinstance(team_id, str):
        team_id = ""
    if not isinstance(email, str):
        email = ""
    return {
        "slack_user_id": user_id,
        "slack_team_id": team_id,
        "slack_email": email,
    }
