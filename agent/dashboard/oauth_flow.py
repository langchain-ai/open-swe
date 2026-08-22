"""The browser half of an OAuth round-trip: base URLs, cookies, state nonces.

GitHub sign-in, Slack linking and Notion linking all run the same shape — mint
a nonce, park it in a path-scoped cookie, send the browser off with a state JWT
carrying only the nonce's hash, then prove on the way back that cookie and
state agree. Only the cookie's name and path differ between them.
"""

import hmac
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, Response
from starlette.requests import HTTPConnection

from ..config import dashboard_api_base_url, dashboard_api_is_https, dashboard_base_url
from .oauth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    STATE_COOKIE_NAME,
    STATE_TTL_SECONDS,
    hash_state_nonce,
)
from .slack_oauth import SLACK_STATE_COOKIE_NAME


def api_base_url() -> str:
    v = dashboard_api_base_url()
    if not v:
        raise HTTPException(500, "DASHBOARD_API_BASE_URL not configured")
    return v


def frontend_base_url() -> str:
    v = dashboard_base_url()
    if not v:
        raise HTTPException(500, "DASHBOARD_BASE_URL not configured")
    return v


def _cookie_security() -> tuple[bool, Literal["lax", "none"]]:
    """Cookie ``secure``/``samesite`` flags derived from the API scheme.

    Production serves the API over HTTPS and the dashboard is a separate
    (cross-site) origin, so the session cookie must be ``Secure; SameSite=None``.
    Local dev runs over ``http://localhost`` where ``Secure`` cookies are
    rejected and the frontend/API are same-site, so fall back to
    ``SameSite=Lax`` without ``Secure``.
    """
    if dashboard_api_is_https():
        return True, "none"
    return False, "lax"


def set_session_cookie(response: Response, jwt_token: str) -> None:
    secure, samesite = _cookie_security()
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    secure, samesite = _cookie_security()
    response.delete_cookie(COOKIE_NAME, path="/", samesite=samesite, secure=secure)


@dataclass(frozen=True)
class OAuthStateCookie:
    """One flow's state nonce: set on the way out, checked on the way back."""

    name: str
    path: str

    def issue(self, response: Response, nonce: str) -> None:
        # SameSite=Lax so the provider's top-level redirect back to the callback
        # still presents this cookie; it is single-purpose and lives only for
        # the duration of one OAuth round-trip.
        secure, _ = _cookie_security()
        response.set_cookie(
            key=self.name,
            value=nonce,
            max_age=STATE_TTL_SECONDS,
            httponly=True,
            secure=secure,
            samesite="lax",
            path=self.path,
        )

    def verify(self, request: HTTPConnection, state_payload: dict[str, Any]) -> str:
        """Prove this callback belongs to the browser that started the flow.

        Returns the state's nonce hash, which doubles as the flow's id for
        providers that parked server-side state under it.
        """
        nonce_hash = state_payload.get("nonce_hash")
        cookie_nonce = request.cookies.get(self.name)
        if (
            not isinstance(nonce_hash, str)
            or not cookie_nonce
            or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash)
        ):
            # Either the cookie went missing (different browser, expired,
            # cookies blocked) or the state was issued for a different session.
            raise HTTPException(400, "oauth state mismatch — please retry")
        return nonce_hash

    def clear(self, response: Response) -> None:
        secure, _ = _cookie_security()
        response.delete_cookie(self.name, path=self.path, samesite="lax", secure=secure)


NOTION_STATE_COOKIE_NAME = "osw_notion_oauth_state"

GITHUB_LOGIN_STATE = OAuthStateCookie(STATE_COOKIE_NAME, "/dashboard/api/auth")
SLACK_LINK_STATE = OAuthStateCookie(SLACK_STATE_COOKIE_NAME, "/dashboard/api/slack")
NOTION_LINK_STATE = OAuthStateCookie(NOTION_STATE_COOKIE_NAME, "/dashboard/api/notion")
