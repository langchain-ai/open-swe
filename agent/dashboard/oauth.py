"""GitHub App OAuth code-exchange and signed-JWT session cookie."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

COOKIE_NAME = "osw_session"
STATE_COOKIE_NAME = "osw_oauth_state"
LINK_STATE_COOKIE_NAME = "osw_link_state"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
STATE_TTL_SECONDS = 600
LINK_STATE_TTL_SECONDS = 600
JWT_ALG = "HS256"

GITHUB_APP_CLIENT_ID = os.environ.get("GITHUB_APP_CLIENT_ID", "")
GITHUB_APP_CLIENT_SECRET = os.environ.get("GITHUB_APP_CLIENT_SECRET", "")


def _secret() -> str:
    s = os.environ.get("DASHBOARD_JWT_SECRET", "")
    if not s:
        raise HTTPException(500, "DASHBOARD_JWT_SECRET not configured")
    return s


def _allowed_redirect_origins() -> set[str]:
    """Origins permitted for the post-login redirect.

    Built from DASHBOARD_BASE_URL plus any DASHBOARD_ALLOWED_ORIGINS entries
    so the dashboard itself and its preview deploys can all be redirect
    targets — but nothing else.
    """
    origins: set[str] = set()
    base = os.environ.get("DASHBOARD_BASE_URL", "").strip()
    if base:
        origins.add(_origin_of(base))
    for entry in os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "").split(","):
        entry = entry.strip()
        if entry:
            origins.add(_origin_of(entry))
    origins.discard("")
    return origins


def _origin_of(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def sanitize_redirect_to(redirect_to: str | None) -> str:
    """Return a safe post-login redirect URL.

    Falls back to DASHBOARD_BASE_URL when the supplied URL's origin isn't
    explicitly allowed. This blocks the open-redirect / phishing primitive
    where an attacker drops their own URL into `?redirect_to=`.
    """
    fallback = os.environ.get("DASHBOARD_BASE_URL", "").strip()
    if not redirect_to:
        return fallback
    candidate_origin = _origin_of(redirect_to)
    if not candidate_origin:
        return fallback
    if candidate_origin in _allowed_redirect_origins():
        return redirect_to
    logger.warning("Rejected redirect_to=%r — origin not in allowlist", redirect_to)
    return fallback


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


def new_state_nonce() -> str:
    """A fresh random nonce used to bind state JWT ↔ browser cookie."""
    return secrets.token_urlsafe(32)


def hash_state_nonce(nonce: str) -> str:
    """HMAC the nonce so the value stored on the wire isn't reversible.

    We compare ``hash_state_nonce(cookie_nonce) == state.nonce_hash`` at
    callback time. Using HMAC over a constant-time digest also gives us
    timing-attack resistance via :func:`hmac.compare_digest` at the call
    site.
    """
    return hmac.new(_secret().encode(), nonce.encode(), hashlib.sha256).hexdigest()


def issue_state(*, redirect_to: str, nonce_hash: str) -> str:
    now = int(time.time())
    payload = {
        "nonce_hash": nonce_hash,
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


def issue_link_state(
    *,
    provider: str,
    github_login: str,
    redirect_to: str,
    nonce_hash: str,
) -> str:
    """State JWT for the Slack/Linear account-linking OAuth round-trip.

    Carries the originating dashboard ``github_login`` so the callback can
    attach the verified provider identity to the right user, independent of
    the session cookie's presence at callback time.
    """
    now = int(time.time())
    payload = {
        "provider": provider,
        "github_login": github_login,
        "redirect_to": redirect_to,
        "nonce_hash": nonce_hash,
        "iat": now,
        "exp": now + LINK_STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALG)


def decode_link_state(state: str) -> dict[str, Any]:
    try:
        return jwt.decode(state, _secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise HTTPException(400, f"invalid link state: {e}") from e


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
    """Return ``(user, work_email)`` for the authenticated user.

    Picks the most useful email by scanning verified addresses on the account:
    when ``WORK_EMAIL_DOMAIN`` is set, the first verified address in that
    domain wins; otherwise we fall back to the primary, then to the public
    email on the user profile. This avoids seeding e.g. ``foo@gmail.com``
    when the user has their work address on GitHub but not as primary.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        u = await client.get("https://api.github.com/user", headers=headers)
        u.raise_for_status()
        user = u.json()
        emails: list[dict[str, Any]] = []
        e = await client.get("https://api.github.com/user/emails", headers=headers)
        if e.status_code == 200:
            payload = e.json()
            if isinstance(payload, list):
                emails = [x for x in payload if isinstance(x, dict)]

    return user, _pick_work_email(emails) or user.get("email")


def _pick_work_email(emails: list[dict[str, Any]]) -> str | None:
    """Pick the best email from GitHub's ``/user/emails`` response."""
    if not emails:
        return None
    work_domain = os.environ.get("WORK_EMAIL_DOMAIN", "").strip().lower()
    verified = [e for e in emails if e.get("verified")]
    if work_domain:
        for entry in verified:
            address = entry.get("email")
            if isinstance(address, str) and address.lower().endswith(f"@{work_domain}"):
                return address
    primary = next((x for x in verified if x.get("primary")), None)
    if primary:
        address = primary.get("email")
        if isinstance(address, str) and address:
            return address
    if verified:
        address = verified[0].get("email")
        if isinstance(address, str) and address:
            return address
    return None
