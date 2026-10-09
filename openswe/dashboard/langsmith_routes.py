"""Sign in with LangSmith: connect/disconnect endpoints and the OAuth browser legs."""

import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.langsmith_oauth import (
    LANGSMITH_STATE_COOKIE_NAME,
    LangSmithOAuthError,
    complete_langsmith_oauth,
    disconnect_langsmith,
    langsmith_status,
    start_langsmith_oauth,
)
from openswe.dashboard.oauth import (
    STATE_TTL_SECONDS,
    DesktopConnectExchange,
    cookie_security,
    decode_state,
    desktop_callback_url,
    desktop_handoff_from_state,
    frontend_base_url,
    hash_state_nonce,
    issue_connect_handoff,
    issue_state,
    new_state_nonce,
    redeem_connect_handoff,
    require_session,
    sanitize_redirect_to,
    valid_handoff_challenge,
)
from openswe.utils.dashboard_links import dashboard_api_base_url

router = APIRouter(tags=["langsmith"])
_COOKIE_PATH = "/dashboard/api/langsmith"


def _clear_state_cookie(response: Response) -> None:
    secure, _ = cookie_security()
    response.delete_cookie(
        LANGSMITH_STATE_COOKIE_NAME, path=_COOKIE_PATH, samesite="lax", secure=secure
    )


async def _complete(login: str, nonce_hash: str, code: str) -> None:
    try:
        await complete_langsmith_oauth(login, nonce_hash, code)
    except LangSmithOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/my-credentials/langsmith")
async def get_my_langsmith_status(session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    return await langsmith_status(session["sub"])


@router.delete("/my-credentials/langsmith")
@audit_endpoint
async def disconnect_my_langsmith(session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    await disconnect_langsmith(session["sub"])
    return await langsmith_status(session["sub"])


@router.get("/langsmith/login")
async def langsmith_login(
    redirect_to: str | None = None,
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
    session: dict[str, Any] = SESSION_DEP,
) -> RedirectResponse:
    nonce = new_state_nonce()
    nonce_hash = hash_state_nonce(nonce)
    state = issue_state(
        redirect_to=sanitize_redirect_to(
            redirect_to or f"{frontend_base_url()}/my-settings/connections"
        ),
        nonce_hash=nonce_hash,
        handoff_challenge=valid_handoff_challenge(desktop_handoff),
        handoff_port=desktop_port,
    )
    try:
        url = await start_langsmith_oauth(
            session["sub"],
            nonce_hash,
            redirect_uri=f"{dashboard_api_base_url()}{_COOKIE_PATH}/callback",
            state=state,
        )
    except LangSmithOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    response = RedirectResponse(url, status_code=302)
    secure, _ = cookie_security()
    response.set_cookie(
        key=LANGSMITH_STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=_COOKIE_PATH,
    )
    return response


@router.get("/langsmith/callback")
async def langsmith_callback(
    request: Request,
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    state_payload = decode_state(state)
    nonce_hash = state_payload.get("nonce_hash")
    if not isinstance(nonce_hash, str):
        raise HTTPException(400, "oauth state mismatch — please retry")
    if error:
        raise HTTPException(400, f"LangSmith OAuth failed: {error_description or error}")
    if not code:
        raise HTTPException(400, "LangSmith OAuth callback missing code")
    handoff = desktop_handoff_from_state(state_payload)
    if handoff is not None:
        # The system browser has neither the desktop session nor the state
        # cookie, so hand the code back to the app to exchange under its session.
        challenge, port = handoff
        handoff_code = issue_connect_handoff(
            provider="langsmith",
            challenge=challenge,
            claims={"nonce_hash": nonce_hash, "code": code},
        )
        response = RedirectResponse(desktop_callback_url(port, handoff_code), status_code=302)
        _clear_state_cookie(response)
        return response
    session = require_session(request)
    cookie_nonce = request.cookies.get(LANGSMITH_STATE_COOKIE_NAME)
    if not cookie_nonce or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash):
        raise HTTPException(400, "oauth state mismatch — please retry")
    await _complete(session["sub"], nonce_hash, code)
    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()
    response = RedirectResponse(redirect_to, status_code=302)
    _clear_state_cookie(response)
    return response


@router.post("/langsmith/desktop/exchange")
async def langsmith_desktop_exchange(
    body: DesktopConnectExchange,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, bool]:
    """Finish a desktop LangSmith connection with the app's own session."""
    claims = redeem_connect_handoff(provider="langsmith", code=body.code, verifier=body.verifier)
    nonce_hash = claims.get("nonce_hash")
    code = claims.get("code")
    if not isinstance(nonce_hash, str) or not isinstance(code, str):
        raise HTTPException(400, "malformed handoff code")
    await _complete(session["sub"], nonce_hash, code)
    return {"connected": True}
