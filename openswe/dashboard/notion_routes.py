"""Notion connect/disconnect endpoints and the Notion OAuth browser legs."""

import hmac
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse

from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.notion_oauth import (
    NOTION_STATE_COOKIE_NAME,
    NotionOAuthError,
    exchange_notion_code,
    pop_notion_oauth_flow,
    store_notion_oauth_flow,
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
    session_user_id,
    valid_handoff_challenge,
)
from openswe.dashboard.user_credentials import connect_notion, disconnect_notion, get_notion_status
from openswe.slack.dm import send_dm
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_api_base_url

logger = logging.getLogger(__name__)

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


async def _complete_notion_connection(login: str, nonce_hash: str, code: str) -> bool:
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
    return flow.get("slack_origin") is True


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
    redirect_to: str | None = None,
    source: Literal["slack"] | None = None,
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
    session: dict[str, Any] = SESSION_DEP,
) -> RedirectResponse:
    redirect_uri = f"{dashboard_api_base_url()}/dashboard/api/notion/callback"
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
        url = await store_notion_oauth_flow(
            session["sub"],
            nonce_hash,
            redirect_uri=redirect_uri,
            state=state,
            slack_origin=source == "slack",
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
) -> Response:
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
        response: Response = RedirectResponse(
            desktop_callback_url(port, handoff_code), status_code=302
        )
        _clear_notion_state_cookie(response)
        return response

    session = require_session(request)
    cookie_nonce = request.cookies.get(NOTION_STATE_COOKIE_NAME)
    if not cookie_nonce or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash):
        raise HTTPException(400, "oauth state mismatch — please retry")

    slack_origin = await _complete_notion_connection(session["sub"], nonce_hash, code)

    if slack_origin:
        notified = False
        try:
            user_id = session_user_id(session)
            user = await User.get(user_id) if user_id is not None else None
            if user is not None and user.slack_user_id:
                notified = await send_dm(
                    user.slack_user_id,
                    "Notion is now connected to your Open SWE account. "
                    "Return to your conversation and ask me to continue when you're ready. "
                    "This personal connection is only available in your private threads.",
                )
        except Exception:
            logger.exception("Notion connected but Slack confirmation failed")
        if not notified:
            logger.warning("Notion connected without Slack confirmation")
        message = (
            "Notion connected. A confirmation was sent to your linked Slack account. "
            if notified
            else "Notion connected, but we couldn't send a Slack confirmation. "
        )
        response = PlainTextResponse(message + "You can close this tab and return to Slack.")
    else:
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
