"""FastAPI router for the dashboard backend."""

import asyncio
import hmac
import json
import logging
import os
import posixpath
import shlex
from time import perf_counter
from typing import Any, Literal
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

from ..utils.thread_ops import langgraph_url
from ..utils.timing import server_timing_header
from .admin import is_admin
from .agent_instructions import (
    AgentInstructionsCreate,
    AgentInstructionsUpdate,
    create_agent_instructions,
    delete_agent_instructions,
    get_agent_instructions,
    list_agent_instructions,
    set_agent_instructions,
)
from .agent_usage import list_agent_usage_leaderboard
from .analyzer_cron import remove_continual_cron
from .enabled_repos import (
    list_enabled_review_repos,
    set_review_repo_enabled,
)
from .environments import (
    DEFAULT_ENVIRONMENT_SLUG,
    EnvironmentCreate,
    EnvironmentUpdate,
    create_environment,
    delete_environment,
    get_environment,
    list_environment_options,
    list_environments,
    slugify,
    update_environment,
)
from .eval_jobs import (
    get_reviewer_eval_status,
)
from .github_token_auth import admin_session_for_github_token, bearer_github_token
from .notion_oauth import (
    NOTION_STATE_COOKIE_NAME,
    NotionOAuthError,
    exchange_notion_code,
    pop_notion_oauth_flow,
    store_notion_oauth_flow,
)
from .oauth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    STATE_COOKIE_NAME,
    STATE_TTL_SECONDS,
    decode_state,
    decode_terminal_ticket,
    desktop_callback_url,
    enforce_org_login_gate,
    exchange_code,
    fetch_github_user,
    hash_state_nonce,
    issue_desktop_handoff,
    issue_session,
    issue_state,
    issue_terminal_ticket,
    new_state_nonce,
    redeem_desktop_handoff,
    require_same_origin_for_mutations,
    require_session,
    sanitize_redirect_to,
    valid_handoff_challenge,
)
from .oidc_auth import admin_session_for_actions_oidc, is_actions_oidc_token
from .options import (
    FABLE_MODEL_IDS,
    SUPPORTED_MODELS,
    gate_fable_model,
    models_with_profile_context_windows,
)
from .profiles import (
    ProfileUpdate,
    get_profile,
    get_valid_access_token,
    normalize_profile_for_response,
    upsert_access_token_from_github_response,
    upsert_profile,
)
from .repo_access import require_repo_access_for_user
from .repo_cache import (
    REPO_LIST_FRESH_MS,
    read_cached_repos,
    schedule_repo_cache_refresh,
    write_cached_repos,
)
from .repo_snapshots import (
    RepoSnapshotConfigError,
    RepoSnapshotCreate,
    RepoSnapshotUpdate,
    create_repo_snapshot,
    delete_repo_snapshot,
    generate_dockerfile_template,
    get_repo_snapshot,
    is_repo_snapshot_build_stale,
    list_repo_snapshots,
    mark_repo_snapshot_building,
    run_snapshot_build,
    update_repo_snapshot,
)
from .review_api import (
    create_review_comment,
    dry_run_trace_resolution,
    get_review,
    get_review_diff,
    list_review_comments,
    list_reviews,
    proxy_pr_image,
    trigger_re_review,
    update_review_comment,
)
from .review_chat_api import (
    delete_review_chat_thread,
    get_review_chat,
    list_review_chat_threads,
    proxy_review_chat_commands,
    proxy_review_chat_history,
    proxy_review_chat_state,
    proxy_review_chat_stream_events,
)
from .review_style_jobs import (
    cancel_review_style_analysis,
    start_bootstrap_analysis,
    sync_review_style_run_status,
)
from .review_styles import (
    ReviewStyleCreate,
    ReviewStylePromptUpdate,
    create_review_style,
    delete_review_style,
    get_review_style,
    list_review_styles,
    normalize_repo_full_name,
    set_custom_prompt,
)
from .sandbox_settings import (
    SandboxSettingsUpdate,
    get_sandbox_settings,
    upsert_sandbox_settings,
)
from .schedules import (
    ScheduleCreateBody,
    ScheduleUpdateBody,
    create_agent_schedule,
    delete_agent_schedule,
    list_agent_schedules,
    trigger_agent_schedule,
    update_agent_schedule,
)
from .skills import (
    DEFAULT_SKILLS_PAGE_SIZE,
    MAX_SKILLS_PAGE_SIZE,
    SkillCreate,
    SkillUpdate,
    create_organization_skill,
    create_skill,
    delete_organization_skill,
    delete_skill,
    list_organization_skills,
    list_skills,
    update_organization_skill,
    update_skill,
)
from .slack_oauth import (
    SLACK_STATE_COOKIE_NAME,
    build_authorize_url,
    exchange_slack_code,
    fetch_slack_identity,
    slack_oauth_configured,
    verify_team,
)
from .team_credentials import (
    DatadogCredentialsUpdate,
    LangSmithCredentialsUpdate,
    connect_datadog,
    connect_langsmith,
    disconnect_datadog,
    disconnect_langsmith,
    get_team_credentials_status,
)
from .team_settings import (
    TeamSettingsUpdate,
    TranscriptionSettingsUpdate,
    get_team_default_model,
    get_team_default_subagent_model,
    get_team_fable_enabled,
    get_team_settings,
    update_team_transcription_model,
    upsert_team_settings,
)
from .thread_api import (
    ThreadMessageBody,
    ThreadResolveBody,
    admin_cancel_dashboard_thread,
    cancel_dashboard_thread,
    delete_dashboard_thread,
    get_dashboard_terminal_sandbox,
    get_dashboard_thread,
    get_dashboard_thread_branch_diff,
    get_dashboard_thread_recovery_patch,
    get_dashboard_thread_run_diff,
    get_dashboard_thread_state,
    get_dashboard_thread_working_tree_diff,
    list_dashboard_threads,
    list_dashboard_threads_page,
    list_dashboard_threads_sidebar,
    proxy_dashboard_thread_commands,
    proxy_dashboard_thread_history,
    proxy_dashboard_thread_run_cancel,
    proxy_dashboard_thread_stream_events,
    resolve_dashboard_thread,
    send_dashboard_message,
    stream_dashboard_thread,
)
from .user_credentials import (
    CurrentsCredentialsUpdate,
    UserLangSmithCredentialsUpdate,
    connect_currents,
    connect_notion,
    disconnect_currents,
    disconnect_notion,
    get_currents_status,
    get_notion_status,
)
from .user_credentials import (
    connect_langsmith as connect_user_langsmith,
)
from .user_credentials import (
    disconnect_langsmith as disconnect_user_langsmith,
)
from .user_credentials import (
    get_langsmith_status as get_user_langsmith_status,
)
from .user_instructions import (
    UserInstructionsUpdate,
    delete_user_instructions,
    get_user_instructions,
    set_user_instructions,
)
from .user_mappings import (
    delete_mapping,
    get_mapping,
    list_mappings,
    upsert_mapping,
)
from .voice import transcribe_audio

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dashboard/api",
    tags=["dashboard"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_GITHUB_API_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
_CLOUD_TERMINAL_SLOTS = asyncio.Semaphore(20)
_CLOUD_TERMINAL_SUBPROTOCOL = "open-swe-terminal"
# Module-level so a local harness can point the browser leg at a fake consent
# page and still run the real login/callback code.
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_SKIPPABLE_INSTALLATION_REPO_STATUS_CODES = frozenset({403, 404})


def _session_is_admin(session: dict[str, Any]) -> bool:
    return is_admin(session.get("email"), login=session.get("sub"))


def _require_admin(session: dict[str, Any]) -> dict[str, Any]:
    if not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    return session


_SESSION_DEP = Depends(require_session)


def _admin_session(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    return _require_admin(session)


_ADMIN_DEP = Depends(_admin_session)


async def _admin_session_or_ci_token(request: Request) -> dict[str, Any]:
    """Admin gate that also accepts CI credentials: an Actions OIDC token, or an
    admin's GitHub personal access token."""
    token = bearer_github_token(request)
    if token:
        if is_actions_oidc_token(token):
            return await admin_session_for_actions_oidc(token)
        return await admin_session_for_github_token(token)
    return _require_admin(require_session(request))


_ADMIN_OR_TOKEN_DEP = Depends(_admin_session_or_ci_token)


async def _filter_repo_records_for_user(
    login: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        full_name = record.get("full_name")
        if not isinstance(full_name, str):
            continue
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        out.append(record)
    return out


def _api_base_url() -> str:
    v = os.environ.get("DASHBOARD_API_BASE_URL", "").rstrip("/")
    if not v:
        raise HTTPException(500, "DASHBOARD_API_BASE_URL not configured")
    return v


def _frontend_base_url() -> str:
    v = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
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
    if os.environ.get("DASHBOARD_API_BASE_URL", "").startswith("https://"):
        return True, "none"
    return False, "lax"


def _set_session_cookie(response: Response, jwt_token: str) -> None:
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


def _set_state_cookie(response: Response, nonce: str) -> None:
    # SameSite=Lax so GitHub's top-level redirect back to /auth/callback
    # still presents this cookie; the cookie is single-purpose and lives
    # only for the duration of one OAuth round-trip.
    secure, _ = _cookie_security()
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/dashboard/api/auth",
    )


def _clear_state_cookie(response: Response) -> None:
    secure, _ = _cookie_security()
    response.delete_cookie(
        STATE_COOKIE_NAME, path="/dashboard/api/auth", samesite="lax", secure=secure
    )


def _set_slack_state_cookie(response: Response, nonce: str) -> None:
    secure, _ = _cookie_security()
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
    secure, _ = _cookie_security()
    response.delete_cookie(
        SLACK_STATE_COOKIE_NAME, path="/dashboard/api/slack", samesite="lax", secure=secure
    )


def _set_notion_state_cookie(response: Response, nonce: str) -> None:
    secure, _ = _cookie_security()
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
    secure, _ = _cookie_security()
    response.delete_cookie(
        NOTION_STATE_COOKIE_NAME, path="/dashboard/api/notion", samesite="lax", secure=secure
    )


@router.get("/auth/login")
async def auth_login(
    request: Request,
    redirect_to: str | None = None,
    desktop: bool = False,
    desktop_handoff: str | None = None,
    desktop_port: int | None = Query(default=None, ge=1024, le=65535),
) -> RedirectResponse:
    client_id = os.environ.get("GITHUB_APP_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(500, "GITHUB_APP_CLIENT_ID not configured")
    safe_redirect = sanitize_redirect_to(redirect_to) or _frontend_base_url()

    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=safe_redirect,
        nonce_hash=hash_state_nonce(nonce),
        handoff_challenge=valid_handoff_challenge(desktop_handoff),
        handoff_port=desktop_port,
    )
    api_base_url = _api_base_url()
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
    _set_state_cookie(response, nonce)
    return response


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str) -> Response:
    state_payload = decode_state(state)
    state_nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(STATE_COOKIE_NAME)
    is_desktop = isinstance(state_payload.get("handoff_challenge"), str) and isinstance(
        state_payload.get("handoff_port"), int
    )
    if not is_desktop and (
        not isinstance(state_nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), state_nonce_hash)
    ):
        # Either the cookie went missing (different browser, expired,
        # cookies blocked) or the state was issued for a different session.
        raise HTTPException(400, "oauth state mismatch — please retry login")

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or _frontend_base_url()

    token_data = await exchange_code(code)
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str):
        raise HTTPException(400, "oauth exchange missing access_token")
    user, email = await fetch_github_user(access_token)
    login = user.get("login")
    if not login:
        raise HTTPException(400, "could not resolve GitHub login")

    await enforce_org_login_gate(login)

    await upsert_access_token_from_github_response(login, email or "", token_data)

    challenge = state_payload.get("handoff_challenge")
    port = state_payload.get("handoff_port")
    if isinstance(challenge, str) and isinstance(port, int):
        # Desktop login runs in the user's own browser, so the session belongs to
        # the app rather than to this browser: hand back a PKCE-bound code the
        # app redeems for one, and leave no session cookie behind here.
        handoff = issue_desktop_handoff(
            login=login,
            email=email,
            avatar_url=user.get("avatar_url"),
            challenge=challenge,
        )
        response = RedirectResponse(desktop_callback_url(port, handoff), status_code=302)
        _clear_state_cookie(response)
        return response

    session_jwt = issue_session(login=login, email=email, avatar_url=user.get("avatar_url"))
    response = RedirectResponse(redirect_to, status_code=302)
    _set_session_cookie(response, session_jwt)
    _clear_state_cookie(response)
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
    secure, samesite = _cookie_security()
    response.delete_cookie(COOKIE_NAME, path="/", samesite=samesite, secure=secure)
    return response


@router.get("/me")
async def me(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    return {
        "login": session["sub"],
        "email": session.get("email"),
        "avatar_url": session.get("avatar_url"),
        "is_admin": _session_is_admin(session),
        "slack_oauth_enabled": slack_oauth_configured(),
    }


@router.get("/me/instructions")
async def api_get_my_instructions(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    record = await get_user_instructions(login)
    return record or {"login": login, "instructions": ""}


@router.put("/me/instructions")
async def api_put_my_instructions(
    body: UserInstructionsUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    return await set_user_instructions(login, body.instructions, updated_by=login)


@router.delete("/me/instructions")
async def api_delete_my_instructions(
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await delete_user_instructions(session["sub"])
    return Response(status_code=204)


@router.get("/options")
async def options() -> dict[str, Any]:
    agent_model, agent_effort = await get_team_default_model("agent")
    subagent_model, subagent_effort = await get_team_default_subagent_model("agent")
    fable_enabled = await get_team_fable_enabled()
    # Never advertise a default that isn't in the selectable list: when Fable is
    # off, gate a stale Fable default down to its non-Fable fallback so the Cloud
    # Agents page (and the PUT /profile it drives) don't choke on it.
    agent_model, agent_effort = gate_fable_model(
        agent_model, agent_effort, fable_enabled=fable_enabled
    )
    subagent_model, subagent_effort = gate_fable_model(
        subagent_model, subagent_effort, fable_enabled=fable_enabled
    )
    models = (
        SUPPORTED_MODELS
        if fable_enabled
        else [m for m in SUPPORTED_MODELS if m["id"] not in FABLE_MODEL_IDS]
    )
    return {
        "models": models_with_profile_context_windows(models),
        "default_agent_model": agent_model,
        "default_agent_reasoning_effort": agent_effort,
        "default_agent_subagent_model": subagent_model,
        "default_agent_subagent_reasoning_effort": subagent_effort,
    }


@router.get("/profile")
async def get_my_profile(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    profile = await get_profile(session["sub"])
    if not profile:
        return {}
    return normalize_profile_for_response(profile)


@router.put("/profile")
async def put_my_profile(
    update: ProfileUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    update.validate_pairing()
    if not await get_team_fable_enabled():
        if (
            update.default_model in FABLE_MODEL_IDS
            or update.default_subagent_model in FABLE_MODEL_IDS
        ):
            raise HTTPException(400, "Fable is disabled for this workspace")
    return await upsert_profile(session["sub"], session.get("email") or "", update)


@router.get("/my-mapping")
async def get_my_mapping(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """Return the logged-in user's own GitHub↔Slack mapping (or empty)."""
    mapping = await get_mapping(session["sub"])
    return mapping or {}


@router.get("/my-credentials/currents")
async def get_my_currents_status(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await get_currents_status(session["sub"])
    return status.get("currents", {"connected": False})


@router.put("/my-credentials/currents")
async def connect_my_currents(
    update: CurrentsCredentialsUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await connect_currents(session["sub"], update)
    return status.get("currents", {"connected": False})


@router.delete("/my-credentials/currents")
async def disconnect_my_currents(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await disconnect_currents(session["sub"])
    return status.get("currents", {"connected": False})


@router.get("/my-credentials/langsmith")
async def get_my_langsmith_status(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await get_user_langsmith_status(session["sub"])
    return status.get("langsmith", {"connected": False})


@router.put("/my-credentials/langsmith")
async def connect_my_langsmith(
    update: UserLangSmithCredentialsUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await connect_user_langsmith(session["sub"], update)
    return status.get("langsmith", {"connected": False})


@router.delete("/my-credentials/langsmith")
async def disconnect_my_langsmith(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await disconnect_user_langsmith(session["sub"])
    return status.get("langsmith", {"connected": False})


@router.get("/my-credentials/notion")
async def get_my_notion_status(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await get_notion_status(session["sub"])
    return status.get("notion", {"connected": False})


@router.delete("/my-credentials/notion")
async def disconnect_my_notion(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await disconnect_notion(session["sub"])
    return status.get("notion", {"connected": False})


@router.get("/notion/login")
async def notion_login(
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    redirect_uri = f"{_api_base_url()}/dashboard/api/notion/callback"
    nonce = new_state_nonce()
    nonce_hash = hash_state_nonce(nonce)
    state = issue_state(
        redirect_to=f"{_frontend_base_url()}/my-settings",
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
    _set_notion_state_cookie(response, nonce)
    return response


@router.get("/notion/callback")
async def notion_callback(
    request: Request,
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    state_payload = decode_state(state)
    nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(NOTION_STATE_COOKIE_NAME)
    if (
        not isinstance(nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash)
    ):
        raise HTTPException(400, "oauth state mismatch — please retry")

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

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or _frontend_base_url()
    response = RedirectResponse(redirect_to, status_code=302)
    _clear_notion_state_cookie(response)
    return response


@router.get("/slack/login")
async def slack_login(
    _session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    """Start the Sign in with Slack flow to link the current GitHub account."""
    if not slack_oauth_configured():
        raise HTTPException(500, "Slack OAuth is not configured")
    redirect_uri = f"{_api_base_url()}/dashboard/api/slack/callback"
    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=f"{_frontend_base_url()}/my-settings",
        nonce_hash=hash_state_nonce(nonce),
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
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    """Link the verified Slack identity to the logged-in GitHub user.

    The Slack member id and email come from Slack's verified OIDC claims, so a
    user can only ever link their own Slack account — no self-asserted values.
    """
    state_payload = decode_state(state)
    nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(SLACK_STATE_COOKIE_NAME)
    if (
        not isinstance(nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash)
    ):
        raise HTTPException(400, "oauth state mismatch — please retry")

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or _frontend_base_url()
    redirect_uri = f"{_api_base_url()}/dashboard/api/slack/callback"

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
    _clear_slack_state_cookie(response)
    return response


@router.get("/team-settings")
async def api_get_team_settings(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_team_settings()


@router.put("/team-settings/transcription")
async def api_put_transcription_settings(
    update: TranscriptionSettingsUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await update_team_transcription_model(update.transcription_model)


@router.put("/team-settings")
async def api_put_team_settings(
    update: TeamSettingsUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await upsert_team_settings(update)


@router.get("/team-credentials")
async def api_get_team_credentials(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await get_team_credentials_status()


@router.put("/team-credentials/datadog")
async def api_connect_datadog(
    update: DatadogCredentialsUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await connect_datadog(update)


@router.delete("/team-credentials/datadog")
async def api_disconnect_datadog(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await disconnect_datadog()


@router.put("/team-credentials/langsmith")
async def api_connect_langsmith(
    update: LangSmithCredentialsUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await connect_langsmith(update)


@router.delete("/team-credentials/langsmith")
async def api_disconnect_langsmith(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await disconnect_langsmith()


class EnabledReviewRepoUpdate(BaseModel):
    full_name: str
    enabled: bool


@router.get("/enabled-review-repos")
async def api_list_enabled_review_repos(
    _session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, list[str]]:
    return {"repos": await list_enabled_review_repos()}


@router.put("/enabled-review-repos")
async def api_set_enabled_review_repo(
    update: EnabledReviewRepoUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, list[str]]:
    repos = await set_review_repo_enabled(update.full_name, update.enabled)
    return {"repos": repos}


@router.get("/sandbox-settings")
async def api_get_sandbox_settings(
    _admin: dict[str, Any] = _ADMIN_OR_TOKEN_DEP,
) -> dict[str, Any]:
    return await get_sandbox_settings()


@router.put("/sandbox-settings")
async def api_set_sandbox_settings(
    body: SandboxSettingsUpdate,
    _admin: dict[str, Any] = _ADMIN_OR_TOKEN_DEP,
) -> dict[str, Any]:
    return await upsert_sandbox_settings(body, updated_by=_admin.get("sub"))


@router.get("/repo-snapshots")
async def api_list_repo_snapshots(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> list[dict[str, Any]]:
    return await list_repo_snapshots()


@router.get("/repo-snapshots/template")
async def api_repo_snapshot_template(
    full_name: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, str]:
    try:
        return {"dockerfile": generate_dockerfile_template(normalize_repo_full_name(full_name))}
    except RepoSnapshotConfigError as e:
        raise HTTPException(500, str(e)) from e


@router.post("/repo-snapshots")
async def api_create_repo_snapshot(
    body: RepoSnapshotCreate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    try:
        return await create_repo_snapshot(body.full_name, _admin["sub"])
    except RepoSnapshotConfigError as e:
        raise HTTPException(500, str(e)) from e


@router.get("/repo-snapshots/{full_name:path}")
async def api_get_repo_snapshot(
    full_name: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    record = await get_repo_snapshot(normalize_repo_full_name(full_name))
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    return record


@router.put("/repo-snapshots/{full_name:path}")
async def api_update_repo_snapshot(
    full_name: str,
    body: RepoSnapshotUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await update_repo_snapshot(normalize_repo_full_name(full_name), body)


@router.post("/repo-snapshots/{full_name:path}/build")
async def api_build_repo_snapshot(
    full_name: str,
    background_tasks: BackgroundTasks,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    record = await get_repo_snapshot(full_name)
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    if not (record.get("dockerfile") or "").strip():
        raise HTTPException(400, "dockerfile is empty")
    if record.get("status") == "building" and not is_repo_snapshot_build_stale(record):
        raise HTTPException(409, "a build is already in progress")
    record = await mark_repo_snapshot_building(full_name)
    background_tasks.add_task(run_snapshot_build, full_name)
    return record


@router.delete("/repo-snapshots/{full_name:path}")
async def api_delete_repo_snapshot(
    full_name: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    record = await get_repo_snapshot(full_name)
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    await delete_repo_snapshot(full_name)
    return Response(status_code=204)


def _normalized_slug(raw: str) -> str:
    try:
        return slugify(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/environments")
async def api_list_environments(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return {
        "environments": await list_environments(),
        "default_slug": DEFAULT_ENVIRONMENT_SLUG,
    }


@router.post("/environments")
async def api_create_environment(
    body: EnvironmentCreate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    try:
        return await create_environment(body, _admin["sub"])
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/environments/options")
async def api_environment_options(
    _session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """Pickable environments for any signed-in user: names only, no prompts."""
    return {
        "environments": await list_environment_options(),
        "default_slug": DEFAULT_ENVIRONMENT_SLUG,
    }


@router.get("/environments/{slug}")
async def api_get_environment(
    slug: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    record = await get_environment(_normalized_slug(slug))
    if not record:
        raise HTTPException(404, "environment not found")
    return record


@router.put("/environments/{slug}")
async def api_update_environment(
    slug: str,
    body: EnvironmentUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    try:
        return await update_environment(_normalized_slug(slug), body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/environments/{slug}")
async def api_delete_environment(
    slug: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> Response:
    if not await delete_environment(_normalized_slug(slug)):
        raise HTTPException(404, "environment not found")
    return Response(status_code=204)


@router.get("/admin/user-mappings")
async def admin_list_user_mappings(
    page: int = 1,
    page_size: int = 20,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    records = await list_mappings()
    total = len(records)
    start = (page - 1) * page_size
    items = records[start : start + page_size]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/admin/user-mappings/{github_login}")
async def admin_delete_user_mapping(
    github_login: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, bool]:
    deleted = await delete_mapping(github_login)
    return {"deleted": deleted}


@router.get("/admin/evals/reviewer")
async def admin_get_reviewer_eval(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    """Read-only status for the reviewer eval (triggered from the GitHub Action)."""
    return await get_reviewer_eval_status()


def _next_link_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    # GitHub Link header is comma-separated: '<url>; rel="next", <url>; rel="last"'
    for part in link_header.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) >= 2 and 'rel="next"' in segments[1] and segments[0].startswith("<"):
            return segments[0][1:-1]
    return None


def _github_api_http_exception(status_code: int) -> HTTPException:
    if status_code == 401:
        return HTTPException(401, "github token expired, re-login required")
    if status_code == 403:
        return HTTPException(403, "github API forbidden")
    if status_code == 404:
        return HTTPException(404, "github API resource not found")
    return HTTPException(502, f"github API error ({status_code})")


async def _paginate(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    items_key: str | None,
    cap: int = 1000,
) -> list[dict[str, Any]]:
    """Follow ``Link: rel="next"`` until exhausted (or cap reached).

    ``items_key`` is the JSON key holding the list when the endpoint returns
    a wrapper object (e.g. ``/user/installations`` returns
    ``{"total_count": N, "installations": [...]}``). When ``None`` the
    response body itself is treated as the list.
    """
    out: list[dict[str, Any]] = []
    next_url: str | None = url
    first = True
    while next_url and len(out) < cap:
        params = {"per_page": "100"} if first else None
        try:
            r = await client.get(next_url, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("GitHub API timed out while paginating %s", next_url)
            raise HTTPException(503, "github API request timed out") from exc
        except httpx.RequestError as exc:
            logger.warning("GitHub API request failed while paginating %s: %s", next_url, exc)
            raise HTTPException(502, "github API request failed") from exc
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "GitHub API returned %s while paginating %s",
                r.status_code,
                next_url,
            )
            raise _github_api_http_exception(r.status_code) from exc
        body = r.json()
        page = body.get(items_key, []) if items_key else body
        if isinstance(page, list):
            out.extend(page)
        next_url = _next_link_url(r.headers.get("Link"))
        first = False
    return out


async def _fetch_user_installations_and_repos(
    login: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve the installations and repos a user can access via the GitHub App.

    Paginates both ``/user/installations`` and per-installation
    ``/user/installations/{id}/repositories`` so users with multiple
    installations or >30 accessible repos get the complete set. Shared by the
    ``/repos`` endpoint and the reviews access filter.
    """
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=_GITHUB_API_TIMEOUT) as client:
        try:
            installations = await _paginate(
                client,
                "https://api.github.com/user/installations",
                headers=headers,
                items_key="installations",
            )
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            token = await get_valid_access_token(login, force_refresh=True)
            if not token:
                raise HTTPException(401, "github token expired, re-login required") from exc
            headers["Authorization"] = f"Bearer {token}"
            installations = await _paginate(
                client,
                "https://api.github.com/user/installations",
                headers=headers,
                items_key="installations",
            )
        repositories: list[dict[str, Any]] = []
        for inst in installations:
            inst_id = inst.get("id")
            if inst_id is None:
                continue
            try:
                repos = await _paginate(
                    client,
                    f"https://api.github.com/user/installations/{inst_id}/repositories",
                    headers=headers,
                    items_key="repositories",
                )
            except HTTPException as exc:
                if exc.status_code in _SKIPPABLE_INSTALLATION_REPO_STATUS_CODES:
                    logger.warning(
                        "Skipping installation %s repository list: %s", inst_id, exc.detail
                    )
                    continue
                raise
            repositories.extend(repos)
    return installations, repositories


async def accessible_repo_full_names(login: str) -> frozenset[str]:
    """Lowercased ``owner/name`` of repos the user can currently access.

    Resolved fresh on every call (a fixed, repo-count-independent burst of
    GitHub calls) rather than cached. ``/reviews`` uses this set to decide
    which private PR metadata a user may see, so it's an authorization
    boundary: a stale set would leak repo/PR titles, branches, authors and
    finding counts for repos the user just lost access to.
    """
    _, repositories = await _fetch_user_installations_and_repos(login)
    return frozenset(
        repo["full_name"].lower() for repo in repositories if isinstance(repo.get("full_name"), str)
    )


async def _build_repo_payload(login: str) -> dict[str, Any]:
    installations, repositories = await _fetch_user_installations_and_repos(login)
    payload = {
        "installations": [
            {
                "id": i.get("id"),
                "account": (i.get("account") or {}).get("login"),
                "account_type": (i.get("account") or {}).get("type"),
            }
            for i in installations
        ],
        "repositories": [
            {"full_name": r.get("full_name"), "private": r.get("private", False)}
            for r in repositories
            if r.get("full_name")
        ],
    }
    await write_cached_repos(login, payload)
    return payload


@router.get("/repos")
async def list_repos(
    refresh: bool = False,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """List repos where Open SWE is installed and the user has access.

    Served from the per-login cache (stale-while-revalidate) unless
    ``refresh=true``, because the fan-out over every installation takes 10s+
    for users with hundreds of accessible repos.
    """
    login = session["sub"]
    if not refresh:
        cached = await read_cached_repos(login)
        if cached is not None:
            payload, age_ms = cached
            if age_ms > REPO_LIST_FRESH_MS:
                schedule_repo_cache_refresh(login, lambda: _build_repo_payload(login))
            return payload
    return await _build_repo_payload(login)


@router.get("/review-styles")
async def api_list_review_styles(
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    records = await _filter_repo_records_for_user(session["sub"], await list_review_styles())
    out: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") == "running":
            synced = await sync_review_style_run_status(record["full_name"])
            out.append(synced)
        else:
            out.append(record)
    return out


REVIEWS_PAGE_SIZE = 20


@router.get("/reviews")
async def api_list_reviews(
    page: int = 0,
    mine: bool = True,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    accessible = await accessible_repo_full_names(login)

    async def is_accessible(summary: dict[str, Any]) -> bool:
        return summary["full_name"].lower() in accessible

    page = max(page, 0)
    reviews, has_more = await list_reviews(
        REVIEWS_PAGE_SIZE,
        offset=page * REVIEWS_PAGE_SIZE,
        author=login if mine else None,
        is_accessible=is_accessible,
    )
    return {"reviews": reviews, "page": page, "has_more": has_more}


@router.get("/reviews/{owner}/{repo}/{pr_number}")
async def api_get_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/diff")
async def api_get_review_diff(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_diff(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/image")
async def api_get_review_image(
    owner: str,
    repo: str,
    pr_number: int,
    url: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await proxy_pr_image(owner, repo, pr_number, url)


@router.post("/reviews/{owner}/{repo}/{pr_number}/re-review")
async def api_re_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await trigger_re_review(owner, repo, pr_number, session["sub"])


@router.post("/reviews/{owner}/{repo}/{pr_number}/resolve-trace")
async def api_resolve_trace(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await dry_run_trace_resolution(owner, repo, pr_number)


class ReviewCommentCreate(BaseModel):
    path: str
    line: int
    side: Literal["LEFT", "RIGHT"]
    body: str
    start_line: int | None = None
    start_side: Literal["LEFT", "RIGHT"] | None = None


@router.get("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_list_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await list_review_comments(owner, repo, pr_number)


@router.post("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_create_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: ReviewCommentCreate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    # Post as the signed-in user (their user-to-server token), so the comment is
    # attributed to them rather than the Open SWE app.
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub re-auth required")
    return await create_review_comment(
        owner,
        repo,
        pr_number,
        token=token,
        path=comment.path,
        line=comment.line,
        side=comment.side,
        body=body,
        start_line=comment.start_line,
        start_side=comment.start_side,
    )


class ReviewCommentUpdate(BaseModel):
    body: str


@router.patch("/reviews/{owner}/{repo}/{pr_number}/comments/{comment_id}")
async def api_update_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    comment: ReviewCommentUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub re-auth required")
    return await update_review_comment(
        owner,
        repo,
        pr_number,
        comment_id,
        token=token,
        viewer_login=session["sub"],
        body=body,
    )


# --- PR chat (sandbox-less ``chat`` graph) -----------------------------------
# The frontend points a LangGraph StreamProvider at the base
# ``/reviews/{owner}/{repo}/{pr_number}/chat``; the SDK then issues the
# ``/threads/{id}/{commands,stream/events,state,history}`` calls proxied below.


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat")
async def api_get_review_chat(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_chat(owner, repo, pr_number, session["sub"])


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads")
async def api_list_review_chat_threads(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    threads = await list_review_chat_threads(owner, repo, pr_number, session["sub"])
    return {"threads": threads}


@router.delete("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}")
async def api_delete_review_chat_thread(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    await delete_review_chat_thread(owner, repo, pr_number, session["sub"], thread_id)
    return Response(status_code=204)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/commands")
async def api_review_chat_commands(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_commands(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/stream/events")
async def api_review_chat_stream_events(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> StreamingResponse:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    stream = await proxy_review_chat_stream_events(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/state")
async def api_review_chat_state(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    status_code, content, media_type = await proxy_review_chat_state(
        owner, repo, pr_number, session["sub"], thread_id
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/history")
async def api_review_chat_history(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_history(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/review-styles")
async def api_create_review_style(
    body: ReviewStyleCreate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await create_review_style(body.full_name, session["sub"])


@router.get("/review-styles/{full_name:path}")
async def api_get_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.get("status") == "running":
        record = await sync_review_style_run_status(full_name)
    return record


@router.put("/review-styles/{full_name:path}")
async def api_update_review_style_prompt(
    full_name: str,
    body: ReviewStylePromptUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    return await set_custom_prompt(full_name, body.custom_prompt)


@router.post("/review-styles/{full_name:path}/analyze")
async def api_analyze_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    token = await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        record = await create_review_style(full_name, session["sub"])
    if record.get("status") == "running":
        record = await sync_review_style_run_status(full_name)
        if record.get("status") == "running":
            raise HTTPException(409, "analysis already running")
    return await start_bootstrap_analysis(
        full_name,
        github_token=token,
        created_by=session["sub"],
    )


@router.post("/review-styles/{full_name:path}/cancel")
async def api_cancel_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    return await cancel_review_style_analysis(full_name)


@router.delete("/review-styles/{full_name:path}")
async def api_delete_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.get("status") == "running":
        await cancel_review_style_analysis(full_name)
    await remove_continual_cron(full_name)
    await delete_review_style(full_name)
    return Response(status_code=204)


@router.get("/agent-instructions")
async def api_list_agent_instructions(
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    return await _filter_repo_records_for_user(session["sub"], await list_agent_instructions())


@router.post("/agent-instructions")
async def api_create_agent_instructions(
    body: AgentInstructionsCreate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await create_agent_instructions(body.full_name, session["sub"])


@router.get("/agent-instructions/{full_name:path}")
async def api_get_agent_instructions(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_agent_instructions(full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    return record


@router.put("/agent-instructions/{full_name:path}")
async def api_update_agent_instructions(
    full_name: str,
    body: AgentInstructionsUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    return await set_agent_instructions(full_name, body.instructions)


@router.delete("/agent-instructions/{full_name:path}")
async def api_delete_agent_instructions(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_agent_instructions(full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    await delete_agent_instructions(full_name)
    return Response(status_code=204)


@router.get("/skills")
async def api_list_skills(
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await list_skills(session["sub"], limit=limit, offset=offset)


@router.post("/skills")
async def api_create_skill(
    body: SkillCreate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await create_skill(session["sub"], body)


@router.put("/skills/{name}")
async def api_update_skill(
    name: str,
    body: SkillUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await update_skill(session["sub"], name, body)


@router.delete("/skills/{name}")
async def api_delete_skill(
    name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await delete_skill(session["sub"], name)
    return Response(status_code=204)


@router.get("/organization-skills")
async def api_list_organization_skills(
    limit: int = Query(DEFAULT_SKILLS_PAGE_SIZE, ge=1, le=MAX_SKILLS_PAGE_SIZE),
    cursor: str | None = Query(None, max_length=256),
    _session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await list_organization_skills(limit=limit, cursor=cursor)


@router.post("/organization-skills")
async def api_create_organization_skill(
    body: SkillCreate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await create_organization_skill(body)


@router.put("/organization-skills/{name}")
async def api_update_organization_skill(
    name: str,
    body: SkillUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await update_organization_skill(name, body)


@router.delete("/organization-skills/{name}")
async def api_delete_organization_skill(
    name: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> Response:
    await delete_organization_skill(name)
    return Response(status_code=204)


@router.get("/agent-usage-leaderboard")
async def api_agent_usage_leaderboard(
    period: str | None = "30d",
    limit: int = 10,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await list_agent_usage_leaderboard(
        period=period,
        limit=limit,
        current_login=session["sub"],
        current_email=session.get("email"),
    )


@router.get("/schedules")
async def api_list_schedules(
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    return await list_agent_schedules(session["sub"], email=session.get("email"))


@router.post("/schedules")
async def api_create_schedule(
    body: ScheduleCreateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await create_agent_schedule(
        session["sub"],
        body,
        email=session.get("email"),
        allow_admin_thread=_session_is_admin(session),
    )


@router.patch("/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: str,
    body: ScheduleUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await update_agent_schedule(
        schedule_id,
        session["sub"],
        body,
        email=session.get("email"),
        allow_admin_thread=_session_is_admin(session),
    )


@router.post("/schedules/{schedule_id}/trigger")
async def api_trigger_schedule(
    schedule_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await trigger_agent_schedule(schedule_id, session["sub"], email=session.get("email"))


@router.delete("/schedules/{schedule_id}")
async def api_delete_schedule(
    schedule_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await delete_agent_schedule(schedule_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)


@router.get("/threads")
async def api_list_threads(
    all: bool = False,
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    if all and not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    return await list_dashboard_threads(session["sub"], email=session.get("email"), include_all=all)


@router.get("/threads/sidebar")
async def api_list_threads_sidebar(
    active_limit: int = 50,
    resolved_limit: int = 20,
    active_thread_id: str | None = None,
    include_automations: bool = False,
    all: bool = False,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    if all and not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    timings: dict[str, float] = {}
    counts: dict[str, int] = {}
    started = perf_counter()
    payload = await list_dashboard_threads_sidebar(
        session["sub"],
        email=session.get("email"),
        active_limit=active_limit,
        resolved_limit=resolved_limit,
        active_thread_id=active_thread_id,
        include_automations=include_automations,
        include_all=all,
        timings=timings,
        counts=counts,
    )
    timings["total"] = (perf_counter() - started) * 1000
    header = server_timing_header(timings, counts)
    logger.info("thread sidebar timings login=%s %s", session["sub"], header)
    return JSONResponse(payload, headers={"Server-Timing": header})


@router.get("/threads/page")
async def api_list_threads_page(
    limit: int = 25,
    offset: int = 0,
    all: bool = False,
    resolved: bool | None = None,
    viewed: bool | None = None,
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    if all and not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    return await list_dashboard_threads_page(
        session["sub"],
        email=session.get("email"),
        limit=limit,
        offset=offset,
        include_all=all,
        resolved=resolved,
        viewed=viewed,
        source=source,
        status=status,
        query=q,
        scope=scope,
        automation_id=automation_id,
    )


@router.get("/threads/{thread_id}")
async def api_get_thread(
    thread_id: str,
    mark_viewed: bool = True,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread(
        thread_id,
        session["sub"],
        email=session.get("email"),
        mark_viewed=mark_viewed,
    )


def _cloud_terminal_websocket_url(thread_id: str) -> str:
    parsed = urlsplit(langgraph_url())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(500, "invalid LangGraph URL for cloud terminal")
    path = f"{parsed.path.rstrip('/')}/dashboard/api/threads/{quote(thread_id, safe='')}/terminal"
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _cloud_terminal_session(websocket: WebSocket, thread_id: str) -> dict[str, Any]:
    offered = [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]
    if len(offered) != 2 or offered[0] != _CLOUD_TERMINAL_SUBPROTOCOL:
        raise HTTPException(401, "invalid terminal ticket")
    return decode_terminal_ticket(offered[1], thread_id=thread_id)


@router.post("/threads/{thread_id}/terminal/connect")
async def api_thread_terminal_connection(
    thread_id: str,
    response: Response,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, str]:
    await get_dashboard_terminal_sandbox(thread_id, session["sub"], email=session.get("email"))
    response.headers["Cache-Control"] = "no-store"
    return {
        "url": _cloud_terminal_websocket_url(thread_id),
        "protocol": _CLOUD_TERMINAL_SUBPROTOCOL,
        "ticket": issue_terminal_ticket(
            login=session["sub"], email=session.get("email"), thread_id=thread_id
        ),
    }


async def _cloud_terminal(websocket: WebSocket, thread_id: str, session: dict[str, Any]) -> None:
    if os.environ.get("SANDBOX_TYPE", "langsmith") != "langsmith":
        await websocket.close(code=1008, reason="Cloud terminal requires a LangSmith sandbox")
        return
    try:
        sandbox_id, repo_name = await get_dashboard_terminal_sandbox(
            thread_id, session["sub"], email=session.get("email")
        )
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return

    await websocket.accept(subprotocol=_CLOUD_TERMINAL_SUBPROTOCOL)
    client = handle = None
    try:
        await asyncio.wait_for(_CLOUD_TERMINAL_SLOTS.acquire(), timeout=0.01)
    except TimeoutError:
        await websocket.close(code=1013, reason="Cloud terminal capacity reached")
        return
    try:
        from ..integrations.langsmith import connect_async_langsmith_sandbox

        client, sandbox = await connect_async_langsmith_sandbox(sandbox_id)
        cwd = posixpath.join("/workspace", repo_name) if repo_name else "/workspace"
        if not (await sandbox.run(f"test -d {shlex.quote(cwd)}")).success:
            cwd = "/workspace"
        handle = await sandbox.run(
            "exec ${SHELL:-/bin/bash} -l",
            cwd=cwd,
            timeout=0,
            idle_timeout=0,
            kill_on_disconnect=True,
            pty=True,
            wait=False,
        )

        async def output() -> None:
            assert handle is not None
            async for chunk in handle:
                await websocket.send_text(json.dumps({"type": "output", "data": chunk.data}))
            result = await handle.result
            await websocket.send_text(json.dumps({"type": "exit", "exitCode": result.exit_code}))

        async def input_() -> None:
            assert handle is not None
            while True:
                message = await websocket.receive_json()
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "input" and isinstance(message.get("data"), str):
                    data = message["data"]
                    if len(data.encode()) <= 64 * 1024:
                        await handle.send_input(data)
                elif message.get("type") == "resize":
                    cols, rows = message.get("cols"), message.get("rows")
                    if (
                        isinstance(cols, int)
                        and not isinstance(cols, bool)
                        and 1 <= cols <= 500
                        and isinstance(rows, int)
                        and not isinstance(rows, bool)
                        and 1 <= rows <= 500
                        and handle.pid is not None
                    ):
                        await sandbox.run(f"stty cols {cols} rows {rows} < /proc/{handle.pid}/fd/0")

        output_task = asyncio.create_task(output())
        input_task = asyncio.create_task(input_())
        done, pending = await asyncio.wait(
            {output_task, input_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cloud terminal failed for thread %s: %s", thread_id, type(exc).__name__)
        try:
            await websocket.send_text(
                json.dumps({"type": "error", "message": "Cloud terminal disconnected"})
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        if handle is not None:
            await handle.kill()
        if client is not None:
            await client.aclose()
        _CLOUD_TERMINAL_SLOTS.release()


@router.websocket("/threads/{thread_id}/terminal")
async def api_thread_terminal(websocket: WebSocket, thread_id: str) -> None:
    try:
        session = _cloud_terminal_session(websocket, thread_id)
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:123])
        return
    await _cloud_terminal(websocket, thread_id, session)


@router.get("/threads/{thread_id}/recovery.patch")
async def api_get_thread_recovery_patch(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    content, filename = await get_dashboard_thread_recovery_patch(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )
    return Response(
        content=content,
        media_type="text/x-diff",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/threads/{thread_id}/working-tree-diff")
async def api_get_thread_working_tree_diff(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_working_tree_diff(
        thread_id, session["sub"], email=session.get("email")
    )


@router.get("/threads/{thread_id}/run-diff")
async def api_get_thread_run_diff(
    thread_id: str,
    turn_key: str,
    max_files: int = Query(200, ge=1, le=200),
    include_content: bool = True,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_run_diff(
        thread_id,
        session["sub"],
        turn_key=turn_key,
        max_files=max_files,
        include_content=include_content,
        email=session.get("email"),
    )


@router.get("/threads/{thread_id}/branch-diff")
async def api_get_thread_branch_diff(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_branch_diff(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


# The pre-branch-diff name, kept for desktop bundles already in the wild.
@router.get("/threads/{thread_id}/pr-diff")
async def api_get_thread_pr_diff(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_branch_diff(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


@router.post("/voice/transcriptions")
async def create_voice_transcription(
    request: Request, session: dict[str, Any] = _SESSION_DEP
) -> dict[str, str]:
    return {"text": await transcribe_audio(request)}


@router.post("/threads/{thread_id}/messages")
async def api_send_thread_message(
    thread_id: str,
    body: ThreadMessageBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await send_dashboard_message(thread_id, session["sub"], body, email=session.get("email"))


@router.post("/threads/{thread_id}/resolve")
async def api_resolve_thread(
    thread_id: str,
    body: ThreadResolveBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await resolve_dashboard_thread(
        thread_id,
        session["sub"],
        resolved=body.resolved,
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def api_cancel_thread_run(
    thread_id: str,
    run_id: str,
    session: dict[str, Any] = _SESSION_DEP,
    wait: str = "0",
    action: str = "interrupt",
) -> Response:
    status_code, content, media_type = await proxy_dashboard_thread_run_cancel(
        thread_id,
        run_id,
        session["sub"],
        wait=wait,
        action=action,
        email=session.get("email"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/cancel")
async def api_cancel_thread(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await cancel_dashboard_thread(thread_id, session["sub"], email=session.get("email"))


@router.post("/admin/threads/{thread_id}/cancel")
async def admin_cancel_thread(
    thread_id: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await admin_cancel_dashboard_thread(thread_id)


@router.delete("/threads/{thread_id}")
async def api_delete_thread(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await delete_dashboard_thread(thread_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)


@router.get("/threads/{thread_id}/state")
async def api_get_thread_state(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    timings: dict[str, float] = {}
    started = perf_counter()
    payload = await get_dashboard_thread_state(
        thread_id, session["sub"], email=session.get("email"), timings=timings
    )
    timings["total"] = (perf_counter() - started) * 1000
    header = server_timing_header(timings)
    logger.info("thread state timings thread_id=%s %s", thread_id, header)
    return JSONResponse(payload, headers={"Server-Timing": header})


@router.post("/threads/{thread_id}/stream/events")
async def api_thread_stream_events(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> StreamingResponse:
    body = await request.body()
    stream = await proxy_dashboard_thread_stream_events(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/threads/{thread_id}/commands")
async def api_thread_commands(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_dashboard_thread_commands(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/history")
async def api_thread_history(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_dashboard_thread_history(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.get("/threads/{thread_id}/stream")
async def api_stream_thread(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> StreamingResponse:
    last_event_id = request.headers.get("last-event-id")

    async def event_generator():
        async for chunk in stream_dashboard_thread(
            thread_id, session["sub"], email=session.get("email"), last_event_id=last_event_id
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
