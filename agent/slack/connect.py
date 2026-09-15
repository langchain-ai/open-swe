"""Sign in with Slack endpoints that link a Slack identity to a GitHub login."""

import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from agent.dashboard.deps import SESSION_DEP
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
from agent.dashboard.user_mappings import upsert_mapping
from agent.slack.oauth import (
    SLACK_STATE_COOKIE_NAME,
    build_authorize_url,
    exchange_slack_code,
    fetch_slack_identity,
    slack_base_url,
    slack_oauth_configured,
    verify_team,
)

router = APIRouter(tags=["slack"])


def _set_slack_state_cookie(response: Response, nonce: str) -> None:
    secure, _ = cookie_security()
    response.set_cookie(
        key=SLACK_STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/dashboard/api/slack",
    )


def _clear_slack_state_cookie(response: Response) -> None:
    secure, _ = cookie_security()
    response.delete_cookie(
        SLACK_STATE_COOKIE_NAME, path="/dashboard/api/slack", samesite="lax", secure=secure
    )


@router.get("/slack/login")
async def slack_login(
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
    _session: dict[str, Any] = SESSION_DEP,
) -> RedirectResponse:
    """Start the Sign in with Slack flow to link the current GitHub account."""
    if not slack_oauth_configured():
        raise HTTPException(500, "Slack OAuth is not configured")
    redirect_uri = f"{slack_base_url()}/dashboard/api/slack/callback"
    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=f"{frontend_base_url()}/my-settings",
        nonce_hash=hash_state_nonce(nonce),
        handoff_challenge=valid_handoff_challenge(desktop_handoff),
        handoff_port=desktop_port,
    )
    response = RedirectResponse(
        build_authorize_url(redirect_uri=redirect_uri, state=state), status_code=302
    )
    _set_slack_state_cookie(response, nonce)
    return response


@router.get("/slack/callback")
async def slack_callback(
    request: Request,
    code: str,
    state: str,
) -> RedirectResponse:
    """Link the verified Slack identity to the logged-in GitHub user.

    The Slack member id and email come from Slack's verified OIDC claims, so a
    user can only ever link their own Slack account — no self-asserted values.
    """
    state_payload = decode_state(state)
    handoff = desktop_handoff_from_state(state_payload)

    if handoff is not None:
        # Same as the Notion flow: hand the verified identity back over the
        # loopback port, for the app to redeem under the session it holds.
        challenge, port = handoff
        slack_user_id, work_email = await _verified_slack_identity(code)
        handoff_code = issue_connect_handoff(
            provider="slack",
            challenge=challenge,
            claims={"slack_user_id": slack_user_id, "email": work_email},
        )
        response = RedirectResponse(desktop_callback_url(port, handoff_code), status_code=302)
        _clear_slack_state_cookie(response)
        return response

    session = require_session(request)
    nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(SLACK_STATE_COOKIE_NAME)
    if (
        not isinstance(nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash)
    ):
        raise HTTPException(400, "oauth state mismatch — please retry")

    slack_user_id, work_email = await _verified_slack_identity(code)
    await upsert_mapping(
        github_login=session["sub"],
        work_email=work_email,
        slack_user_id=slack_user_id,
        source="slack_oauth",
        status="active",
    )

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()
    response = RedirectResponse(redirect_to, status_code=302)
    _clear_slack_state_cookie(response)
    return response


async def _verified_slack_identity(code: str) -> tuple[str, str]:
    """Resolve an authorization code to a Slack member id and verified email."""
    redirect_uri = f"{slack_base_url()}/dashboard/api/slack/callback"
    identity = await fetch_slack_identity(await exchange_slack_code(code, redirect_uri))
    verify_team(identity)
    if not identity.email or not identity.email_verified:
        raise HTTPException(400, "your Slack account has no verified email to link")
    return identity.user_id, identity.email


@router.post("/slack/desktop/exchange")
async def slack_desktop_exchange(
    body: DesktopConnectExchange,
    session: dict[str, Any] = SESSION_DEP,
) -> dict[str, Any]:
    """Finish a desktop Slack link with the app's own session."""
    claims = redeem_connect_handoff(provider="slack", code=body.code, verifier=body.verifier)
    slack_user_id = claims.get("slack_user_id")
    email = claims.get("email")
    if not isinstance(slack_user_id, str) or not isinstance(email, str):
        raise HTTPException(400, "malformed handoff code")

    await upsert_mapping(
        github_login=session["sub"],
        work_email=email,
        slack_user_id=slack_user_id,
        source="slack_oauth",
        status="active",
    )
    return {"connected": True}
