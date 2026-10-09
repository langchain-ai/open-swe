"""Web login, logout, desktop handoff redemption, and the session identity."""

import hmac
import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from openswe.config import ENV
from openswe.database import postgres
from openswe.slack.oauth import slack_base_url, slack_oauth_configured
from openswe.users import User
from openswe.utils.build_info import build_info
from openswe.utils.web_links import web_api_base_url
from openswe.web.admin import configured_admins, is_admin
from openswe.web.deps import SESSION_DEP, session_is_admin
from openswe.web.dev_login import GhUnavailable, dev_login_enabled, gh_credentials
from openswe.web.oauth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    STATE_COOKIE_NAME,
    GithubUser,
    clear_state_cookie,
    cookie_security,
    decode_state,
    desktop_callback_url,
    desktop_handoff_from_state,
    enforce_github_login_gate,
    exchange_code,
    fetch_github_user,
    frontend_base_url,
    hash_state_nonce,
    issue_desktop_handoff,
    issue_session,
    issue_state,
    new_state_nonce,
    rebind_session,
    redeem_desktop_handoff,
    sanitize_redirect_to,
    session_user_id,
    set_session_cookie,
    set_state_cookie,
    valid_handoff_challenge,
)
from openswe.web.profiles import (
    upsert_access_token,
    upsert_access_token_from_github_response,
)
from openswe.web.user_preferences import get_user_preferences

router = APIRouter(tags=["auth"])

logger = logging.getLogger(__name__)

# Module-level so a local harness can point the browser leg at a fake consent
# page and still run the real login/callback code.
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"


@router.get("/auth/login")
async def auth_login(
    request: Request,
    redirect_to: str | None = None,
    desktop: bool = False,
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
) -> RedirectResponse:
    client_id = ENV.GITHUB_APP_CLIENT_ID.get()
    if not client_id:
        # Locally there is no App to redirect to, and the `gh` CLI already holds
        # the only credential the per-user reads need.
        if dev_login_enabled() and not desktop:
            return RedirectResponse(
                "/api/auth/dev-login?"
                + urlencode({"redirect_to": sanitize_redirect_to(redirect_to)}),
                status_code=302,
            )
        raise HTTPException(500, "GITHUB_APP_CLIENT_ID not configured")
    safe_redirect = sanitize_redirect_to(redirect_to) or frontend_base_url()

    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=safe_redirect,
        nonce_hash=hash_state_nonce(nonce),
        handoff_challenge=valid_handoff_challenge(desktop_handoff),
        handoff_port=desktop_port,
    )
    api_base_url = web_api_base_url()
    if desktop:
        forwarded_proto = request.headers.get("x-forwarded-proto", "").partition(",")[0].strip()
        scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme
        api_base_url = str(request.base_url.replace(scheme=scheme)).rstrip("/")
    redirect_uri = f"{api_base_url}/dashboard/api/auth/callback"
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    url = f"{GITHUB_AUTHORIZE_URL}?{query}"
    response = RedirectResponse(url, status_code=302)
    set_state_cookie(response, nonce)
    return response


@router.get("/auth/dev-login")
async def auth_dev_login(redirect_to: str | None = None) -> Response:
    """Sign in locally as the `gh` CLI's user, with no GitHub App involved.

    Refused outside `langgraph dev`, and still subject to the login allowlist.
    """
    if not dev_login_enabled():
        raise HTTPException(404, "not found")
    try:
        credentials = await gh_credentials()
    except GhUnavailable as exc:
        raise HTTPException(503, f"gh CLI unavailable: {exc}") from exc

    await enforce_github_login_gate(credentials.login)
    signed_in = await User.sign_in(
        "github",
        credentials.external_id,
        login=credentials.login,
        email=credentials.email,
        display_name=credentials.display_name,
        avatar_url=credentials.avatar_url,
    )
    await upsert_access_token(credentials.login, credentials.email, credentials.token)
    session_jwt = issue_session(
        login=credentials.login,
        email=credentials.email or None,
        avatar_url=credentials.avatar_url or None,
        user_id=str(signed_in.id),
    )
    logger.info("Signed in from the gh CLI", extra={"github_login": credentials.login})
    response = RedirectResponse(
        sanitize_redirect_to(redirect_to) or frontend_base_url(), status_code=302
    )
    set_session_cookie(response, session_jwt)
    return response


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str) -> Response:
    state_payload = decode_state(state)
    state_nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(STATE_COOKIE_NAME)
    handoff = desktop_handoff_from_state(state_payload)
    if handoff is None and (
        not isinstance(state_nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), state_nonce_hash)
    ):
        # Either the cookie went missing (different browser, expired,
        # cookies blocked) or the state was issued for a different session.
        raise HTTPException(400, "oauth state mismatch — please retry login")

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()

    token_data = await exchange_code(code)
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str):
        raise HTTPException(400, "oauth exchange missing access_token")
    user, email = await fetch_github_user(access_token)
    login = user.login
    if not login:
        raise HTTPException(400, "could not resolve GitHub login")

    await enforce_github_login_gate(login)

    user_id = await _signed_in_user_id(user, email)
    await upsert_access_token_from_github_response(login, email or "", token_data)

    if handoff is not None:
        # Desktop login runs in the user's own browser, so the session belongs to
        # the app rather than to this browser: hand back a PKCE-bound code the
        # app redeems for one, and leave no session cookie behind here.
        challenge, port = handoff
        handoff_code = issue_desktop_handoff(
            login=login,
            email=email,
            avatar_url=user.avatar_url,
            challenge=challenge,
            user_id=user_id,
        )
        response = RedirectResponse(desktop_callback_url(port, handoff_code), status_code=302)
        clear_state_cookie(response)
        return response

    session_jwt = issue_session(
        login=login, email=email, avatar_url=user.avatar_url, user_id=user_id
    )
    response = RedirectResponse(redirect_to, status_code=302)
    set_session_cookie(response, session_jwt)
    clear_state_cookie(response)
    return response


async def _signed_in_user_id(user: GithubUser, email: str | None) -> str:
    signed_in = await User.sign_in(
        "github",
        str(user.id),
        login=user.login,
        email=email or "",
        display_name=user.name or "",
        avatar_url=user.avatar_url or "",
        admin=is_admin(email, login=user.login) if configured_admins() else None,
    )
    return str(signed_in.id)


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
    secure, samesite = cookie_security()
    response.delete_cookie(COOKIE_NAME, path="/", samesite=samesite, secure=secure)
    return response


@router.get("/me")
async def me(response: Response, session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    # Best-effort: this endpoint is what the web app boots on, and it must
    # answer from the session alone when the database is unreachable.
    user = None
    try:
        user = await User.for_session(session_user_id(session), session["sub"])
    except Exception:
        logger.warning(
            "Could not read the signed-in user's row",
            extra={"github_login": session["sub"]},
            exc_info=True,
        )
    if user is not None and session_user_id(session) != user.id:
        # The web app boots here, so every stale cookie is repaired on its next load.
        set_session_cookie(response, rebind_session(session, str(user.id)))
        logger.info(
            "Rebound a stale session to its user",
            extra={"github_login": session["sub"], "user_id": str(user.id)},
        )
    preferences = await get_user_preferences(session["sub"])
    return {
        "login": session["sub"],
        "email": session.get("email") or (user.email or None if user else None),
        "avatar_url": session.get("avatar_url"),
        "user_id": str(user.id) if user else session.get("user_id"),
        "slack_user_id": (user.slack_user_id or None) if user else None,
        "is_admin": session_is_admin(session),
        "follow_up_behavior": preferences["follow_up_behavior"],
        # Whether new threads are stamped `transcript: v2` (`openswe/threads/runs.py`),
        # so the thread the UI seeds after `run.start` can carry the same stamp.
        "transcript_recording": postgres.configured(),
        "slack_oauth_enabled": slack_oauth_configured(),
        "api_base_url": web_api_base_url(),
        "slack_base_url": slack_base_url(),
        "build_info": build_info(),
    }
