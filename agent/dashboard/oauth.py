"""GitHub App OAuth code-exchange and signed-JWT session cookie."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import HTTPException, Request

from agent.utils.github_org_membership import is_user_active_org_member

logger = logging.getLogger(__name__)

COOKIE_NAME = "osw_session"
STATE_COOKIE_NAME = "osw_oauth_state"
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


def allowed_dashboard_origins() -> set[str]:
    """Origins permitted for dashboard frontend requests and post-login redirects.

    Built from DASHBOARD_BASE_URL plus any DASHBOARD_ALLOWED_ORIGINS entries
    so the dashboard itself and its preview deploys are allowed — but nothing
    else.
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
    """Normalize a URL or Origin header value to ``scheme://host[:port]``."""
    trimmed = url.strip().rstrip("/")
    if not trimmed or trimmed.lower() == "null":
        return ""
    parsed = urlparse(trimmed)
    if not parsed.scheme or not parsed.hostname:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is None:
        return f"{scheme}://{host}"
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    if default_port is not None and port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


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
    if candidate_origin in allowed_dashboard_origins():
        return redirect_to
    logger.warning("Rejected redirect_to=%r — origin not in allowlist", redirect_to)
    return fallback


def _allowed_login_orgs() -> frozenset[str]:
    """Orgs whose members may log in to the dashboard.

    Reuses the webhook-side ``ALLOWED_GITHUB_ORGS`` allowlist so deployments
    configure a single org gate. When empty the dashboard login gate is
    disabled (fail-open) to preserve existing deployments.
    """
    return frozenset(
        org.strip().lower()
        for org in os.environ.get("ALLOWED_GITHUB_ORGS", "").split(",")
        if org.strip()
    )


async def enforce_org_login_gate(login: str) -> None:
    """Reject dashboard login for users outside the allowed GitHub org(s).

    No-op when ``ALLOWED_GITHUB_ORGS`` is unset. Otherwise the user must be an
    active member of at least one configured org; membership is checked with
    the GitHub App installation token (fail-closed on any API error).
    """
    orgs = _allowed_login_orgs()
    if not orgs:
        return
    for org in orgs:
        if await is_user_active_org_member(login, org):
            return
    logger.warning("Rejected dashboard login for %r — not in allowed org(s)", login)
    raise HTTPException(403, "your GitHub account is not a member of an authorized organization")


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
    payload: dict[str, Any] = {
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


# Dashboard route where users manage their GitHub↔Slack link.
PROFILE_SETTINGS_PATH = "/my-settings"


def build_settings_url() -> str | None:
    """Return the dashboard Profile Settings URL, or ``None`` if not configured.

    This is a plain, token-free link: it carries no per-user identity, so it is
    safe to share in a public Slack thread. The user signs in with GitHub from
    their own session and connects Slack via verified OIDC on the settings page.
    """
    frontend_base = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    if not frontend_base:
        return None
    return f"{frontend_base}{PROFILE_SETTINGS_PATH}"


def require_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "not authenticated")
    return decode_session(token)


def request_origin(request: Request) -> str | None:
    """Return the request's origin (scheme + host + port), if present and valid."""
    raw_origin = request.headers.get("origin")
    if raw_origin is not None:
        if raw_origin.strip().lower() == "null":
            return None
        origin = _origin_of(raw_origin)
        return origin if origin else None
    referer = request.headers.get("referer")
    if referer:
        origin = _origin_of(referer)
        return origin if origin else None
    return None


def require_same_origin(request: Request) -> None:
    """Reject cross-site cookie-authenticated mutations (CSRF defense).

    No-op when no dashboard origins are configured (local setups without
    ``DASHBOARD_BASE_URL`` / ``DASHBOARD_ALLOWED_ORIGINS``).
    """
    allowed = allowed_dashboard_origins()
    if not allowed:
        return
    origin = request_origin(request)
    if not origin or origin not in allowed:
        logger.warning(
            "Rejected %s %s — origin %r not in allowlist",
            request.method,
            request.url.path,
            origin,
        )
        raise HTTPException(403, "CSRF check failed")


def require_same_origin_for_mutations(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    require_same_origin(request)


def expires_at_from_github_response(data: dict[str, Any], *, field: str) -> str | None:
    """Convert GitHub ``expires_in`` / ``refresh_token_expires_in`` to an ISO timestamp."""
    raw = data.get(field)
    if not isinstance(raw, int | float) or raw <= 0:
        return None
    return (datetime.now(UTC) + timedelta(seconds=int(raw))).isoformat()


class GithubOAuthError(HTTPException):
    """A GitHub OAuth token endpoint error, carrying GitHub's ``error`` code."""

    def __init__(self, status_code: int, detail: str, *, error_code: str | None = None) -> None:
        super().__init__(status_code, detail)
        self.error_code = error_code


# Error codes GitHub returns when a refresh token can never mint a new access
# token again (the user must re-authorize). Anything else is treated as
# transient so we don't needlessly drop a usable authorization.
UNRECOVERABLE_REFRESH_ERROR_CODES = frozenset({"bad_refresh_token", "unauthorized_client"})


def is_unrecoverable_refresh_error(exc: BaseException) -> bool:
    """Whether ``exc`` means the stored refresh token is permanently dead."""
    return (
        isinstance(exc, GithubOAuthError)
        and (exc.error_code or "") in UNRECOVERABLE_REFRESH_ERROR_CODES
    )


async def _request_github_tokens(body: dict[str, str]) -> dict[str, Any]:
    if not GITHUB_APP_CLIENT_ID or not GITHUB_APP_CLIENT_SECRET:
        raise HTTPException(500, "GitHub App OAuth not configured")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data=body,
        )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise HTTPException(502, "unexpected GitHub OAuth response")
    if data.get("error"):
        raise GithubOAuthError(
            400,
            f"github oauth error: {data.get('error_description') or data['error']}",
            error_code=str(data["error"]),
        )
    return data


async def exchange_code(code: str) -> dict[str, Any]:
    """Exchange an OAuth authorization code for user-to-server tokens."""
    data = await _request_github_tokens(
        {
            "client_id": GITHUB_APP_CLIENT_ID,
            "client_secret": GITHUB_APP_CLIENT_SECRET,
            "code": code,
        }
    )
    if not data.get("access_token"):
        raise HTTPException(400, f"oauth exchange failed: {data}")
    return data


async def refresh_user_access_token(refresh_token: str) -> dict[str, Any]:
    """Rotate an expiring user access token using its refresh token."""
    data = await _request_github_tokens(
        {
            "client_id": GITHUB_APP_CLIENT_ID,
            "client_secret": GITHUB_APP_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )
    if not data.get("access_token"):
        raise HTTPException(400, f"oauth refresh failed: {data}")
    return data


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
