"""Notion connect/disconnect endpoints and the Notion OAuth browser legs."""

import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from agent.dashboard.deps import SESSION_DEP
from agent.dashboard.notion_oauth import (
    NOTION_STATE_COOKIE_NAME,
    NotionOAuthError,
    exchange_notion_code,
    pop_notion_oauth_flow,
    store_notion_oauth_flow,
)
from agent.dashboard.oauth import (
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
from agent.dashboard.user_credentials import connect_notion, disconnect_notion, get_notion_status
from agent.utils.dashboard_links import dashboard_api_base_url

router = APIRouter(tags=["notion"])


def _set_notion_state_cookie(response: Response, nonce: str) -> None:
    secure, _ = cookie_security()
    response.set_cookie(
        key=NOTION_STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/dashboard/api/notion",
    )


def _clear_notion_state_cookie(response: Response) -> None:
    secure, _ = cookie_security()
    response.delete_cookie(
        NOTION_STATE_COOKIE_NAME, path="/dashboard/api/notion", samesite="lax", secure=secure
    )


async def _complete_notion_connection(login: str, nonce_hash: str, code: str) -> None:
    flow = await pop_notion_oauth_flow(login, nonce_hash)
    if flow is None:
        raise HTTPException(400, "oauth flow expired — please retry")
    try:
        token_data = await exchange_notion_code(code, flow)
        await connect_notion(login, token_data, flow)
    except NotionOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/my-credentials/notion")
async def get_my_notion_status(
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    status = await get_notion_status(session["sub"])
    return status.get("notion", {"connected": False})


@router.delete("/my-credentials/notion")
async def disconnect_my_notion(
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    status = await disconnect_notion(session["sub"])
    return status.get("notion", {"connected": False})


@router.get("/notion/login")
async def notion_login(
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
    session: dict[str, Any] = SESSION_DEP,
) -> RedirectResponse:
    redirect_uri = f"{dashboard_api_base_url()}/dashboard/api/notion/callback"
    nonce = new_state_nonce()
    nonce_hash = hash_state_nonce(nonce)
    state = issue_state(
        redirect_to=f"{frontend_base_url()}/my-settings",
        nonce_hash=nonce_hash,
        handoff_challenge=valid_handoff_challenge(desktop_handoff),
        handoff_port=desktop_port,
    )
    try:
        url = await store_notion_oauth_flow(
            session["sub"],
            nonce_hash,
            redirect_uri=redirect_uri,
            state=state,
        )
    except NotionOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    response = RedirectResponse(url, status_code=302)
    _set_notion_state_cookie(response, nonce)
    return response


@router.get("/notion/callback")
async def notion_callback(
    request: Request,
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    state_payload = decode_state(state)
    nonce_hash = state_payload.get("nonce_hash")
    handoff = desktop_handoff_from_state(state_payload)
    if not isinstance(nonce_hash, str):
        raise HTTPException(400, "oauth state mismatch — please retry")
    if error:
        detail = error_description or error
        raise HTTPException(400, f"Notion OAuth failed: {detail}")
    if not code:
        raise HTTPException(400, "Notion OAuth callback missing code")

    if handoff is not None:
        # This browser can't prove it is the login the pending flow is stored
        # under, so carry the code back over the loopback port and exchange it
        # under the session the desktop app already holds.
        challenge, port = handoff
        handoff_code = issue_connect_handoff(
            provider="notion",
            challenge=challenge,
            claims={"nonce_hash": nonce_hash, "code": code},
        )
        response = RedirectResponse(desktop_callback_url(port, handoff_code), status_code=302)
        _clear_notion_state_cookie(response)
        return response

    session = require_session(request)
    cookie_nonce = request.cookies.get(NOTION_STATE_COOKIE_NAME)
    if not cookie_nonce or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash):
        raise HTTPException(400, "oauth state mismatch — please retry")

    await _complete_notion_connection(session["sub"], nonce_hash, code)

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()
    response = RedirectResponse(redirect_to, status_code=302)
    _clear_notion_state_cookie(response)
    return response


@router.post("/notion/desktop/exchange")
async def notion_desktop_exchange(
    body: DesktopConnectExchange,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    """Finish a desktop Notion connection with the app's own session."""
    claims = redeem_connect_handoff(provider="notion", code=body.code, verifier=body.verifier)
    nonce_hash = claims.get("nonce_hash")
    notion_code = claims.get("code")
    if not isinstance(nonce_hash, str) or not isinstance(notion_code, str):
        raise HTTPException(400, "malformed handoff code")

    await _complete_notion_connection(session["sub"], nonce_hash, notion_code)
    return {"connected": True}
