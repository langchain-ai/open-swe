"""FastAPI router for the dashboard backend."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from .admin import is_admin
from .oauth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    decode_state,
    exchange_code,
    fetch_github_user,
    issue_session,
    issue_state,
    require_session,
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


@router.get("/auth/login")
async def auth_login(request: Request, redirect_to: str | None = None) -> RedirectResponse:
    client_id = os.environ.get("GITHUB_APP_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(500, "GITHUB_APP_CLIENT_ID not configured")
    target = redirect_to or _frontend_base_url()
    state = issue_state(redirect_to=target)
    redirect_uri = f"{_api_base_url()}/dashboard/api/auth/callback"
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return RedirectResponse(url, status_code=302)


@router.get("/auth/callback")
async def auth_callback(code: str, state: str) -> RedirectResponse:
    state_payload = decode_state(state)
    redirect_to = state_payload.get("redirect_to") or _frontend_base_url()

    access_token = await exchange_code(code)
    user, email = await fetch_github_user(access_token)
    login = user.get("login")
    if not login:
        raise HTTPException(400, "could not resolve GitHub login")

    await upsert_access_token(login, email or "", access_token)

    session_jwt = issue_session(login=login, email=email, avatar_url=user.get("avatar_url"))
    response = RedirectResponse(redirect_to, status_code=302)
    _set_session_cookie(response, session_jwt)
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


@router.get("/options")
async def options() -> dict[str, Any]:
    return {"models": SUPPORTED_MODELS}


@router.get("/profile")
async def get_my_profile(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    profile = await get_profile(session["sub"])
    if not profile:
        return {}
    profile.pop("encrypted_gh_token", None)
    return profile


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


@router.get("/repos")
async def list_repos(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """List repos where open-swe is installed and the user has access."""
    token = await get_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    installations: list[dict[str, Any]] = []
    repositories: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        ins_resp = await client.get("https://api.github.com/user/installations", headers=headers)
        if ins_resp.status_code == 401:
            raise HTTPException(401, "github token expired, re-login required")
        ins_resp.raise_for_status()
        installations = ins_resp.json().get("installations", []) or []
        for inst in installations:
            inst_id = inst.get("id")
            if inst_id is None:
                continue
            r = await client.get(
                f"https://api.github.com/user/installations/{inst_id}/repositories",
                headers=headers,
            )
            if r.status_code != 200:
                continue
            repositories.extend(r.json().get("repositories", []) or [])
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
