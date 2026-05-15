"""GitHub App OAuth code-exchange and signed-JWT session cookie."""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

COOKIE_NAME = "osw_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
STATE_TTL_SECONDS = 600
JWT_ALG = "HS256"

GITHUB_APP_CLIENT_ID = os.environ.get("GITHUB_APP_CLIENT_ID", "")
GITHUB_APP_CLIENT_SECRET = os.environ.get("GITHUB_APP_CLIENT_SECRET", "")


def _secret() -> str:
    s = os.environ.get("DASHBOARD_JWT_SECRET", "")
    if not s:
        raise HTTPException(500, "DASHBOARD_JWT_SECRET not configured")
    return s


def issue_session(*, login: str, email: str | None, avatar_url: str | None) -> str:
    now = int(time.time())
    payload = {
        "sub": login,
        "email": email,
        "avatar_url": avatar_url,
        "iat": now,
        "exp": now + SESSION_TTL_SECONDS,
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALG)


def decode_session(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid session: {e}") from e


def issue_state(*, redirect_to: str) -> str:
    now = int(time.time())
    payload = {
        "nonce": secrets.token_urlsafe(16),
        "redirect_to": redirect_to,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALG)


def decode_state(state: str) -> dict[str, Any]:
    try:
        return jwt.decode(state, _secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(400, f"invalid state: {e}") from e


def require_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "not authenticated")
    return decode_session(token)


async def exchange_code(code: str) -> str:
    """Exchange an OAuth authorization code for a user-to-server access token."""
    if not GITHUB_APP_CLIENT_ID or not GITHUB_APP_CLIENT_SECRET:
        raise HTTPException(500, "GitHub App OAuth not configured")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_APP_CLIENT_ID,
                "client_secret": GITHUB_APP_CLIENT_SECRET,
                "code": code,
            },
        )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise HTTPException(400, f"oauth exchange failed: {data}")
    return token


async def fetch_github_user(access_token: str) -> tuple[dict[str, Any], str | None]:
    """Return ``(user, primary_email)`` for the authenticated user."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        u = await client.get("https://api.github.com/user", headers=headers)
        u.raise_for_status()
        user = u.json()
        email = user.get("email")
        if not email:
            e = await client.get("https://api.github.com/user/emails", headers=headers)
            if e.status_code == 200:
                primary = next((x for x in e.json() if x.get("primary")), None)
                if primary:
                    email = primary.get("email")
    return user, email
