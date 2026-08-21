"""The user's own third-party connections: Currents, LangSmith, Notion, Slack.

Notion and Slack are linked by an OAuth round-trip rather than a pasted key,
so their login/callback pair lives here next to the status endpoints the
settings page reads.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..authz import SESSION
from ..notion_oauth import (
    NotionOAuthError,
    exchange_notion_code,
    pop_notion_oauth_flow,
    store_notion_oauth_flow,
)
from ..oauth import (
    decode_state,
    hash_state_nonce,
    issue_state,
    new_state_nonce,
    sanitize_redirect_to,
)
from ..oauth_flow import (
    NOTION_LINK_STATE,
    SLACK_LINK_STATE,
    api_base_url,
    frontend_base_url,
)
from ..slack_oauth import (
    build_authorize_url,
    exchange_slack_code,
    fetch_slack_identity,
    slack_oauth_configured,
    verify_team,
)
from ..user_credentials import (
    CurrentsCredentialsUpdate,
    UserLangSmithCredentialsUpdate,
    connect_currents,
    connect_notion,
    disconnect_currents,
    disconnect_notion,
    get_currents_status,
    get_notion_status,
)
from ..user_credentials import (
    connect_langsmith as connect_user_langsmith,
)
from ..user_credentials import (
    disconnect_langsmith as disconnect_user_langsmith,
)
from ..user_credentials import (
    get_langsmith_status as get_user_langsmith_status,
)
from ..user_mappings import upsert_mapping

router = APIRouter()


@router.get("/my-credentials/currents")
async def get_my_currents_status(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await get_currents_status(session["sub"])
    return status.get("currents", {"connected": False})


@router.put("/my-credentials/currents")
async def connect_my_currents(
    update: CurrentsCredentialsUpdate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await connect_currents(session["sub"], update)
    return status.get("currents", {"connected": False})


@router.delete("/my-credentials/currents")
async def disconnect_my_currents(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await disconnect_currents(session["sub"])
    return status.get("currents", {"connected": False})


@router.get("/my-credentials/langsmith")
async def get_my_langsmith_status(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await get_user_langsmith_status(session["sub"])
    return status.get("langsmith", {"connected": False})


@router.put("/my-credentials/langsmith")
async def connect_my_langsmith(
    update: UserLangSmithCredentialsUpdate,
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await connect_user_langsmith(session["sub"], update)
    return status.get("langsmith", {"connected": False})


@router.delete("/my-credentials/langsmith")
async def disconnect_my_langsmith(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await disconnect_user_langsmith(session["sub"])
    return status.get("langsmith", {"connected": False})


@router.get("/my-credentials/notion")
async def get_my_notion_status(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await get_notion_status(session["sub"])
    return status.get("notion", {"connected": False})


@router.delete("/my-credentials/notion")
async def disconnect_my_notion(
    session: dict[str, Any] = SESSION,
) -> dict[str, Any]:
    status = await disconnect_notion(session["sub"])
    return status.get("notion", {"connected": False})


@router.get("/notion/login")
async def notion_login(
    session: dict[str, Any] = SESSION,
) -> RedirectResponse:
    redirect_uri = f"{api_base_url()}/dashboard/api/notion/callback"
    nonce = new_state_nonce()
    nonce_hash = hash_state_nonce(nonce)
    state = issue_state(
        redirect_to=f"{frontend_base_url()}/my-settings",
        nonce_hash=nonce_hash,
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
    NOTION_LINK_STATE.issue(response, nonce)
    return response


@router.get("/notion/callback")
async def notion_callback(
    request: Request,
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: dict[str, Any] = SESSION,
) -> RedirectResponse:
    state_payload = decode_state(state)
    nonce_hash = NOTION_LINK_STATE.verify(request, state_payload)

    flow = await pop_notion_oauth_flow(session["sub"], nonce_hash)
    if flow is None:
        raise HTTPException(400, "oauth flow expired — please retry")
    if error:
        detail = error_description or error
        raise HTTPException(400, f"Notion OAuth failed: {detail}")
    if not code:
        raise HTTPException(400, "Notion OAuth callback missing code")

    try:
        token_data = await exchange_notion_code(code, flow)
        await connect_notion(session["sub"], token_data, flow)
    except NotionOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()
    response = RedirectResponse(redirect_to, status_code=302)
    NOTION_LINK_STATE.clear(response)
    return response


@router.get("/slack/login")
async def slack_login(
    _session: dict[str, Any] = SESSION,
) -> RedirectResponse:
    """Start the Sign in with Slack flow to link the current GitHub account."""
    if not slack_oauth_configured():
        raise HTTPException(500, "Slack OAuth is not configured")
    redirect_uri = f"{api_base_url()}/dashboard/api/slack/callback"
    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=f"{frontend_base_url()}/my-settings",
        nonce_hash=hash_state_nonce(nonce),
    )
    response = RedirectResponse(
        build_authorize_url(redirect_uri=redirect_uri, state=state), status_code=302
    )
    SLACK_LINK_STATE.issue(response, nonce)
    return response


@router.get("/slack/callback")
async def slack_callback(
    request: Request,
    code: str,
    state: str,
    session: dict[str, Any] = SESSION,
) -> RedirectResponse:
    """Link the verified Slack identity to the logged-in GitHub user.

    The Slack member id and email come from Slack's verified OIDC claims, so a
    user can only ever link their own Slack account — no self-asserted values.
    """
    state_payload = decode_state(state)
    SLACK_LINK_STATE.verify(request, state_payload)

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or frontend_base_url()
    redirect_uri = f"{api_base_url()}/dashboard/api/slack/callback"

    access_token = await exchange_slack_code(code, redirect_uri)
    identity = await fetch_slack_identity(access_token)
    verify_team(identity)
    if not identity.email or not identity.email_verified:
        raise HTTPException(400, "your Slack account has no verified email to link")

    await upsert_mapping(
        github_login=session["sub"],
        work_email=identity.email,
        slack_user_id=identity.user_id,
        source="slack_oauth",
        status="active",
    )

    response = RedirectResponse(redirect_to, status_code=302)
    SLACK_LINK_STATE.clear(response)
    return response
