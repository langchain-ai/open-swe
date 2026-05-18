"""FastAPI router for the dashboard backend."""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from ..utils.github_org_membership import is_user_active_org_member
from . import linear_oauth, slack_oauth
from .account_links import (
    Provider,
    delete_link_for_login,
    get_links_for_login,
    upsert_linear_link,
    upsert_slack_link,
)
from .admin import is_admin
from .oauth import (
    COOKIE_NAME,
    LINK_STATE_COOKIE_NAME,
    LINK_STATE_TTL_SECONDS,
    SESSION_TTL_SECONDS,
    STATE_COOKIE_NAME,
    STATE_TTL_SECONDS,
    decode_link_state,
    decode_state,
    exchange_code,
    fetch_github_user,
    hash_state_nonce,
    issue_link_state,
    issue_session,
    issue_state,
    new_state_nonce,
    require_session,
    sanitize_redirect_to,
)
from .options import SUPPORTED_MODELS
from .profiles import (
    ProfileUpdate,
    get_access_token,
    get_profile,
    list_profiles,
    upsert_access_token,
    upsert_profile,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard/api", tags=["dashboard"])


def _require_admin(session: dict[str, Any]) -> dict[str, Any]:
    if not is_admin(session.get("email")):
        raise HTTPException(403, "admin only")
    return session


_SESSION_DEP = Depends(require_session)


def _admin_session(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    return _require_admin(session)


_ADMIN_DEP = Depends(_admin_session)


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


def _set_session_cookie(response: Response, jwt_token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="none",
        path="/",
    )


def _set_state_cookie(response: Response, nonce: str) -> None:
    # SameSite=Lax so GitHub's top-level redirect back to /auth/callback
    # still presents this cookie; the cookie is single-purpose and lives
    # only for the duration of one OAuth round-trip.
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/dashboard/api/auth",
    )


def _clear_state_cookie(response: Response) -> None:
    response.delete_cookie(
        STATE_COOKIE_NAME, path="/dashboard/api/auth", samesite="lax", secure=True
    )


def _set_link_state_cookie(response: Response, nonce: str) -> None:
    response.set_cookie(
        key=LINK_STATE_COOKIE_NAME,
        value=nonce,
        max_age=LINK_STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/dashboard/api/auth",
    )


def _clear_link_state_cookie(response: Response) -> None:
    response.delete_cookie(
        LINK_STATE_COOKIE_NAME, path="/dashboard/api/auth", samesite="lax", secure=True
    )


@router.get("/auth/login")
async def auth_login(request: Request, redirect_to: str | None = None) -> RedirectResponse:
    client_id = os.environ.get("GITHUB_APP_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(500, "GITHUB_APP_CLIENT_ID not configured")
    safe_redirect = sanitize_redirect_to(redirect_to) or _frontend_base_url()

    nonce = new_state_nonce()
    state = issue_state(redirect_to=safe_redirect, nonce_hash=hash_state_nonce(nonce))
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

    access_token = await exchange_code(code)
    user, email = await fetch_github_user(access_token)
    login = user.get("login")
    if not login:
        raise HTTPException(400, "could not resolve GitHub login")

    allowed_org = os.environ.get("ALLOWED_AUTH_ORG", "").strip()
    if allowed_org and not await is_user_active_org_member(login, allowed_org):
        logger.warning(
            "Rejecting dashboard login for %s — not an active member of %s",
            login,
            allowed_org,
        )
        denied_url = f"{_frontend_base_url()}/not-authorized?org={allowed_org}"
        response = RedirectResponse(denied_url, status_code=302)
        _clear_state_cookie(response)
        return response

    await upsert_access_token(login, email or "", access_token)

    session_jwt = issue_session(login=login, email=email, avatar_url=user.get("avatar_url"))
    response = RedirectResponse(redirect_to, status_code=302)
    _set_session_cookie(response, session_jwt)
    _clear_state_cookie(response)
    return response


@router.post("/auth/logout")
async def auth_logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", samesite="none", secure=True)
    return response


@router.get("/me")
async def me(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    return {
        "login": session["sub"],
        "email": session.get("email"),
        "avatar_url": session.get("avatar_url"),
        "is_admin": is_admin(session.get("email")),
    }


def _provider_callback_uri(provider: Provider) -> str:
    return f"{_api_base_url()}/dashboard/api/auth/{provider}/callback"


def _provider_client_creds(provider: Provider) -> tuple[str, str]:
    if provider == "slack":
        return (
            os.environ.get("SLACK_CLIENT_ID", "").strip(),
            os.environ.get("SLACK_CLIENT_SECRET", "").strip(),
        )
    return (
        os.environ.get("LINEAR_CLIENT_ID", "").strip(),
        os.environ.get("LINEAR_CLIENT_SECRET", "").strip(),
    )


def _start_link_flow(
    *,
    provider: Provider,
    github_login: str,
    redirect_to: str | None,
) -> RedirectResponse:
    client_id, client_secret = _provider_client_creds(provider)
    if not client_id or not client_secret:
        raise HTTPException(500, f"{provider} OAuth client credentials not configured")
    safe_redirect = sanitize_redirect_to(redirect_to) or _frontend_base_url()
    nonce = new_state_nonce()
    state = issue_link_state(
        provider=provider,
        github_login=github_login,
        redirect_to=safe_redirect,
        nonce_hash=hash_state_nonce(nonce),
    )
    redirect_uri = _provider_callback_uri(provider)
    if provider == "slack":
        url = slack_oauth.build_authorize_url(
            client_id=client_id, redirect_uri=redirect_uri, state=state
        )
    else:
        url = linear_oauth.build_authorize_url(
            client_id=client_id, redirect_uri=redirect_uri, state=state
        )
    response = RedirectResponse(url, status_code=302)
    _set_link_state_cookie(response, nonce)
    return response


def _validate_link_state(
    *,
    provider: Provider,
    state: str,
    cookie_nonce: str | None,
) -> dict[str, Any]:
    payload = decode_link_state(state)
    if payload.get("provider") != provider:
        raise HTTPException(400, "link state provider mismatch")
    state_nonce_hash = payload.get("nonce_hash")
    if (
        not isinstance(state_nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), state_nonce_hash)
    ):
        raise HTTPException(400, "link state mismatch — please retry")
    return payload


@router.get("/auth/slack/login")
async def slack_link_login(
    request: Request,
    redirect_to: str | None = None,
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    return _start_link_flow(
        provider="slack",
        github_login=session["sub"],
        redirect_to=redirect_to,
    )


@router.get("/auth/slack/callback")
async def slack_link_callback(
    request: Request,
    code: str,
    state: str,
) -> RedirectResponse:
    payload = _validate_link_state(
        provider="slack",
        state=state,
        cookie_nonce=request.cookies.get(LINK_STATE_COOKIE_NAME),
    )
    github_login = payload.get("github_login")
    if not isinstance(github_login, str) or not github_login:
        raise HTTPException(400, "missing github_login in link state")

    access_token = await slack_oauth.exchange_code(
        code=code, redirect_uri=_provider_callback_uri("slack")
    )
    identity = await slack_oauth.fetch_userinfo(access_token)

    await upsert_slack_link(
        github_login=github_login,
        slack_user_id=identity["slack_user_id"],
        slack_team_id=identity["slack_team_id"],
        slack_email=identity.get("slack_email"),
    )

    redirect_to = sanitize_redirect_to(payload.get("redirect_to")) or _frontend_base_url()
    response = RedirectResponse(f"{redirect_to}?linked=slack", status_code=302)
    _clear_link_state_cookie(response)
    return response


@router.get("/auth/linear/login")
async def linear_link_login(
    request: Request,
    redirect_to: str | None = None,
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    return _start_link_flow(
        provider="linear",
        github_login=session["sub"],
        redirect_to=redirect_to,
    )


@router.get("/auth/linear/callback")
async def linear_link_callback(
    request: Request,
    code: str,
    state: str,
) -> RedirectResponse:
    payload = _validate_link_state(
        provider="linear",
        state=state,
        cookie_nonce=request.cookies.get(LINK_STATE_COOKIE_NAME),
    )
    github_login = payload.get("github_login")
    if not isinstance(github_login, str) or not github_login:
        raise HTTPException(400, "missing github_login in link state")

    access_token = await linear_oauth.exchange_code(
        code=code, redirect_uri=_provider_callback_uri("linear")
    )
    identity = await linear_oauth.fetch_viewer_identity(access_token)

    await upsert_linear_link(
        github_login=github_login,
        linear_user_id=identity["linear_user_id"],
        linear_workspace_id=identity["linear_workspace_id"],
        linear_email=identity.get("linear_email"),
    )

    redirect_to = sanitize_redirect_to(payload.get("redirect_to")) or _frontend_base_url()
    response = RedirectResponse(f"{redirect_to}?linked=linear", status_code=302)
    _clear_link_state_cookie(response)
    return response


@router.get("/account-links")
async def list_account_links(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_links_for_login(session["sub"])


@router.delete("/account-links/{provider}")
async def delete_account_link(
    provider: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    provider_typed: Provider
    if provider == "slack":
        provider_typed = "slack"
    elif provider == "linear":
        provider_typed = "linear"
    else:
        raise HTTPException(400, "unknown provider")
    removed = await delete_link_for_login(provider_typed, session["sub"])
    if not removed:
        raise HTTPException(404, f"no {provider} link to delete")
    return Response(status_code=204)


@router.get("/options")
async def options() -> dict[str, Any]:
    return {"models": SUPPORTED_MODELS}


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


@router.get("/admin/profiles")
async def admin_list_profiles(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> list[dict[str, Any]]:
    return await list_profiles()


class AdminProfileUpdate(ProfileUpdate):
    email: str | None = None


@router.put("/admin/profiles/{login}")
async def admin_put_profile(
    login: str,
    update: AdminProfileUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    update.validate_pairing()
    existing = await get_profile(login) or {}
    email = update.email or existing.get("email") or ""
    base = ProfileUpdate(
        default_model=update.default_model,
        reasoning_effort=update.reasoning_effort,
        default_repo=update.default_repo,
    )
    return await upsert_profile(login, email, base)


def _next_link_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    # GitHub Link header is comma-separated: '<url>; rel="next", <url>; rel="last"'
    for part in link_header.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) >= 2 and 'rel="next"' in segments[1] and segments[0].startswith("<"):
            return segments[0][1:-1]
    return None


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
        r = await client.get(next_url, headers=headers, params=params)
        if r.status_code == 401:
            raise HTTPException(401, "github token expired, re-login required")
        r.raise_for_status()
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
    token = await get_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
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
            except HTTPException:
                raise
            except httpx.HTTPStatusError:
                continue
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
