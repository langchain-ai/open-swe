"""Linear user OAuth flow used purely for account-linking verification.

The access token obtained here is used once to call the ``viewer`` GraphQL
query, then discarded. Persistent Linear API calls go through
``agent.utils.linear_app_token.get_linear_app_token`` (workspace install via
``client_credentials``); user OAuth tokens are not retained.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

LINEAR_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
LINEAR_USER_SCOPES = "read"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    return (
        f"{LINEAR_AUTHORIZE_URL}?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={LINEAR_USER_SCOPES}"
        f"&prompt=consent"
        f"&state={state}"
    )


async def exchange_code(*, code: str, redirect_uri: str) -> str:
    client_id = os.environ.get("LINEAR_CLIENT_ID", "")
    client_secret = os.environ.get("LINEAR_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise HTTPException(500, "Linear OAuth client credentials not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            LINEAR_TOKEN_URL,
            data={
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
            },
        )
    if response.status_code != 200:
        logger.warning("Linear token exchange failed: %s %s", response.status_code, response.text)
        raise HTTPException(400, f"Linear token exchange failed: {response.status_code}")
    data = response.json()
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(400, f"Linear token response missing access_token: {data}")
    return token


async def fetch_viewer_identity(access_token: str) -> dict[str, str]:
    """Fetch the authenticated user's identity from Linear via the ``viewer`` query."""
    query = """
    query Viewer {
      viewer {
        id
        email
        name
        organization {
          id
          urlKey
        }
      }
    }
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            LINEAR_GRAPHQL_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"query": query},
        )
    if response.status_code != 200:
        raise HTTPException(400, f"Linear viewer fetch failed: {response.status_code}")
    payload = response.json()
    viewer = (payload.get("data") or {}).get("viewer") or {}
    user_id = viewer.get("id")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(400, "Linear viewer response missing id")
    organization = viewer.get("organization") or {}
    return {
        "linear_user_id": user_id,
        "linear_email": viewer.get("email") or "",
        "linear_workspace_id": organization.get("id") or "",
    }
