"""GitHub sign-in: the browser round-trip, the desktop handoff, and ``/me``."""

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from ...config import github_app_oauth
from ...settings.github_tokens import (
    GithubOAuthError,
    exchange_code,
    upsert_access_token_from_github_response,
)
from ..authz import SESSION, session_is_admin
from ..github_token_auth import fetch_github_identity
from ..oauth import (
    SESSION_TTL_SECONDS,
    decode_state,
    desktop_callback_url,
    enforce_org_login_gate,
    hash_state_nonce,
    issue_desktop_handoff,
    issue_session,
    issue_state,
    new_state_nonce,
    redeem_desktop_handoff,
    sanitize_redirect_to,
    valid_handoff_challenge,
)
from ..oauth_flow import (
    GITHUB_LOGIN_STATE,
    api_base_url,
    clear_session_cookie,
    frontend_base_url,
    set_session_cookie,
)
from ..slack_oauth import slack_oauth_configured

# Module-level so a local harness can point the browser leg at a fake consent
# page and still run the real login/callback code.
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"

router = APIRouter()


@router.get("/auth/login")
async def auth_login(
    request: Request,
    redirect_to: str | None = None,
    desktop: bool = False,
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
) -> RedirectResponse:
    client_id, _ = github_app_oauth()
    if not client_id:
        raise HTTPException(500, "GITHUB_APP_CLIENT_ID not configured")
    safe_redirect = sanitize_redirect_to(redirect_to) or frontend_base_url()

    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=safe_redirect,
        nonce_hash=hash_state_nonce(nonce),
        handoff_challenge=valid_handoff_challenge(desktop_handoff),
        handoff_port=desktop_port,
    )
    base_url = api_base_url()
    if desktop:
        forwarded_proto = request.headers.get("x-forwarded-proto", "").partition(",")[0].strip()
        scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme
        base_url = str(request.base_url.replace(scheme=scheme)).rstrip("/")
    redirect_uri = f"{base_url}/dashboard/api/auth/callback"
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    response = RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{query}", status_code=302)
    GITHUB_LOGIN_STATE.issue(response, nonce)
    return response


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str) -> Response:
    state_payload = decode_state(state)
    GITHUB_LOGIN_STATE.verify(request, state_payload)

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()

    try:
        token_data = await exchange_code(code)
    except GithubOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str):
        raise HTTPException(400, "oauth exchange missing access_token")
    identity = await fetch_github_identity(access_token)

    await enforce_org_login_gate(identity.login)

    await upsert_access_token_from_github_response(identity.login, identity.email or "", token_data)

    challenge = state_payload.get("handoff_challenge")
    port = state_payload.get("handoff_port")
    if isinstance(challenge, str) and isinstance(port, int):
        # Desktop login runs in the user's own browser, so the session belongs to
        # the app rather than to this browser: hand back a PKCE-bound code the
        # app redeems for one, and leave no session cookie behind here.
        handoff = issue_desktop_handoff(
            login=identity.login,
            email=identity.email,
            avatar_url=identity.avatar_url,
            challenge=challenge,
        )
        response = RedirectResponse(desktop_callback_url(port, handoff), status_code=302)
        GITHUB_LOGIN_STATE.clear(response)
        return response

    session_jwt = issue_session(
        login=identity.login, email=identity.email, avatar_url=identity.avatar_url
    )
    response = RedirectResponse(redirect_to, status_code=302)
    set_session_cookie(response, session_jwt)
    GITHUB_LOGIN_STATE.clear(response)
    return response


class DesktopHandoffExchange(BaseModel):
    code: str
    verifier: str


@router.post("/auth/desktop/exchange")
async def auth_desktop_exchange(body: DesktopHandoffExchange) -> dict[str, Any]:
    return {
        "session": redeem_desktop_handoff(code=body.code, verifier=body.verifier),
        "expires_in": SESSION_TTL_SECONDS,
    }


@router.post("/auth/logout")
async def auth_logout() -> Response:
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
async def me(session: dict[str, Any] = SESSION) -> dict[str, Any]:
    return {
        "login": session["sub"],
        "email": session.get("email"),
        "avatar_url": session.get("avatar_url"),
        "is_admin": session_is_admin(session),
        "slack_oauth_enabled": slack_oauth_configured(),
    }
