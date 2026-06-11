"""FastAPI router for the dashboard backend."""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

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
from .agent_usage import (
    list_agent_usage_leaderboard,
    refresh_reviewer_stats_cache,
    refresh_usage_leaderboard_cache,
)
from .analyzer_cron import remove_continual_cron
from .enabled_repos import (
    list_enabled_review_repos,
    set_review_repo_enabled,
)
from .oauth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    STATE_COOKIE_NAME,
    STATE_TTL_SECONDS,
    decode_state,
    enforce_org_login_gate,
    exchange_code,
    fetch_github_user,
    hash_state_nonce,
    issue_session,
    issue_state,
    new_state_nonce,
    require_same_origin_for_mutations,
    require_session,
    sanitize_redirect_to,
)
from .options import SUPPORTED_MODELS
from .profiles import (
    ProfileUpdate,
    get_profile,
    get_valid_access_token,
    upsert_access_token_from_github_response,
    upsert_profile,
)
from .repo_access import require_repo_access_for_user
from .review_api import (
    get_review,
    get_review_diff,
    list_reviews,
    trigger_re_review,
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
from .schedules import (
    ScheduleCreateBody,
    ScheduleUpdateBody,
    create_agent_schedule,
    delete_agent_schedule,
    list_agent_schedules,
    update_agent_schedule,
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
    get_team_default_model,
    get_team_default_subagent_model,
    get_team_settings,
    upsert_team_settings,
)
from .thread_api import (
    ThreadMessageBody,
    cancel_dashboard_thread,
    delete_dashboard_thread,
    get_dashboard_thread,
    get_dashboard_thread_pr_diff,
    get_dashboard_thread_state,
    list_dashboard_threads,
    proxy_dashboard_thread_commands,
    proxy_dashboard_thread_history,
    proxy_dashboard_thread_run_cancel,
    proxy_dashboard_thread_stream_events,
    send_dashboard_message,
    stream_dashboard_thread,
)
from .user_mappings import (
    delete_mapping,
    get_mapping,
    list_mappings,
    upsert_mapping,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dashboard/api",
    tags=["dashboard"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_GITHUB_API_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
_SKIPPABLE_INSTALLATION_REPO_STATUS_CODES = frozenset({403, 404})


def _require_admin(session: dict[str, Any]) -> dict[str, Any]:
    if not is_admin(session.get("email")):
        raise HTTPException(403, "admin only")
    return session


_SESSION_DEP = Depends(require_session)


def _admin_session(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    return _require_admin(session)


_ADMIN_DEP = Depends(_admin_session)


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


def _cookie_security() -> tuple[bool, str]:
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


@router.get("/auth/login")
async def auth_login(
    request: Request,
    redirect_to: str | None = None,
) -> RedirectResponse:
    client_id = os.environ.get("GITHUB_APP_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(500, "GITHUB_APP_CLIENT_ID not configured")
    safe_redirect = sanitize_redirect_to(redirect_to) or _frontend_base_url()

    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=safe_redirect,
        nonce_hash=hash_state_nonce(nonce),
    )
    redirect_uri = f"{_api_base_url()}/dashboard/api/auth/callback"
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    response = RedirectResponse(url, status_code=302)
    _set_state_cookie(response, nonce)
    return response


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    state_payload = decode_state(state)
    state_nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(STATE_COOKIE_NAME)
    if (
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

    session_jwt = issue_session(login=login, email=email, avatar_url=user.get("avatar_url"))
    response = RedirectResponse(redirect_to, status_code=302)
    _set_session_cookie(response, session_jwt)
    _clear_state_cookie(response)
    return response


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
        "is_admin": is_admin(session.get("email")),
        "slack_oauth_enabled": slack_oauth_configured(),
    }


@router.get("/options")
async def options() -> dict[str, Any]:
    agent_model, agent_effort = await get_team_default_model("agent")
    subagent_model, subagent_effort = await get_team_default_subagent_model("agent")
    return {
        "models": SUPPORTED_MODELS,
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
    return profile or {}


@router.put("/profile")
async def put_my_profile(
    update: ProfileUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    update.validate_pairing()
    return await upsert_profile(session["sub"], session.get("email") or "", update)


@router.get("/my-mapping")
async def get_my_mapping(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """Return the logged-in user's own GitHub↔Slack mapping (or empty)."""
    mapping = await get_mapping(session["sub"])
    return mapping or {}


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


@router.get("/repos")
async def list_repos(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """List repos where open-swe is installed and the user has access.

    Paginates both ``/user/installations`` and per-installation
    ``/user/installations/{id}/repositories`` so users with multiple
    installations or >30 accessible repos get the complete set.
    """
    login = session["sub"]
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
    return {
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


@router.get("/reviews")
async def api_list_reviews(
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    login = session["sub"]
    access_cache: dict[str, bool] = {}

    async def is_accessible(summary: dict[str, Any]) -> bool:
        full_name = summary["full_name"]
        if full_name not in access_cache:
            try:
                await require_repo_access_for_user(login, full_name)
                access_cache[full_name] = True
            except HTTPException as exc:
                if exc.status_code not in {403, 404}:
                    raise
                access_cache[full_name] = False
        return access_cache[full_name]

    return await list_reviews(is_accessible=is_accessible)


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


@router.post("/reviews/{owner}/{repo}/{pr_number}/re-review")
async def api_re_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await trigger_re_review(owner, repo, pr_number, session["sub"])


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


@router.get("/agent-usage-leaderboard")
async def api_agent_usage_leaderboard(
    background_tasks: BackgroundTasks,
    period: str | None = "30d",
    limit: int = 10,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await list_agent_usage_leaderboard(
        period=period,
        limit=limit,
        current_login=session["sub"],
        current_email=session.get("email"),
        schedule_usage_refresh=lambda cache_period: background_tasks.add_task(
            refresh_usage_leaderboard_cache, cache_period
        ),
        schedule_reviewer_refresh=lambda cache_period: background_tasks.add_task(
            refresh_reviewer_stats_cache, cache_period
        ),
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
    return await create_agent_schedule(session["sub"], body, email=session.get("email"))


@router.patch("/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: str,
    body: ScheduleUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await update_agent_schedule(
        schedule_id, session["sub"], body, email=session.get("email")
    )


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
    return await list_dashboard_threads(session["sub"], email=session.get("email"), include_all=all)


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


@router.get("/threads/{thread_id}/pr-diff")
async def api_get_thread_pr_diff(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_pr_diff(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/messages")
async def api_send_thread_message(
    thread_id: str,
    body: ThreadMessageBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await send_dashboard_message(thread_id, session["sub"], body, email=session.get("email"))


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
) -> dict[str, Any]:
    return await get_dashboard_thread_state(thread_id, session["sub"], email=session.get("email"))


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
