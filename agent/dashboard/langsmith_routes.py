"""Sign in with LangSmith: connect/disconnect endpoints and the OAuth browser legs."""

import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.langsmith_oauth import (
    LANGSMITH_STATE_COOKIE_NAME,
    LangSmithOAuthError,
    complete_langsmith_oauth,
    disconnect_langsmith,
    langsmith_status,
    start_langsmith_oauth,
)
from agent.dashboard.oauth import (
    STATE_TTL_SECONDS,
    cookie_security,
    decode_state,
    frontend_base_url,
    hash_state_nonce,
    issue_state,
    new_state_nonce,
    require_session,
    sanitize_redirect_to,
)
from agent.utils.dashboard_links import dashboard_api_base_url

router = APIRouter(tags=["langsmith"])
_COOKIE_PATH = "/dashboard/api/langsmith"


def _clear_state_cookie(response: Response) -> None:
    secure, _ = cookie_security()
    response.delete_cookie(
        LANGSMITH_STATE_COOKIE_NAME, path=_COOKIE_PATH, samesite="lax", secure=secure
    )


@router.get("/my-credentials/langsmith")
async def get_my_langsmith_status(session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    return await langsmith_status(session["sub"])


@router.delete("/my-credentials/langsmith")
async def disconnect_my_langsmith(session: dict[str, Any] = SESSION_DEP) -> dict[str, Any]:
    await disconnect_langsmith(session["sub"])
    return await langsmith_status(session["sub"])


@router.get("/langsmith/login")
async def langsmith_login(
    redirect_to: str | None = None,
    session: dict[str, Any] = SESSION_DEP,
) -> RedirectResponse:
    nonce = new_state_nonce()
    nonce_hash = hash_state_nonce(nonce)
    state = issue_state(
        redirect_to=sanitize_redirect_to(redirect_to or f"{frontend_base_url()}/my-settings"),
        nonce_hash=nonce_hash,
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
    session = require_session(request)
    cookie_nonce = request.cookies.get(LANGSMITH_STATE_COOKIE_NAME)
    if not cookie_nonce or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash):
        raise HTTPException(400, "oauth state mismatch — please retry")
    try:
        await complete_langsmith_oauth(session["sub"], nonce_hash, code)
    except LangSmithOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()
    response = RedirectResponse(redirect_to, status_code=302)
    _clear_state_cookie(response)
    return response
