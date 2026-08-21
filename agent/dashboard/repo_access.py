"""GitHub repository access checks for dashboard actions.

Also the single place the dashboard turns a GitHub status code into an
``HTTPException`` and the single place it retries a user-token call after a
401 refresh — both used to exist twice, with different wording.
"""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import HTTPException

from ..utils.github_http import github_client, github_request, github_url
from .github_tokens import get_valid_access_token
from .review_styles import normalize_repo_full_name

T = TypeVar("T")


def github_http_exception(status_code: int) -> HTTPException:
    """Map a GitHub response status onto the error the dashboard returns."""
    if status_code == 401:
        return HTTPException(401, "github token expired, re-login required")
    if status_code == 403:
        return HTTPException(403, "no access to this private repository")
    if status_code == 404:
        return HTTPException(404, "repository not found")
    return HTTPException(502, f"github API error ({status_code})")


def raise_for_github_status(status_code: int) -> None:
    """Raise the mapped ``HTTPException`` unless the status is a 200."""
    if status_code != 200:
        raise github_http_exception(status_code)


async def with_user_github_token(login: str, call: Callable[[str], Awaitable[T]]) -> tuple[str, T]:
    """Run ``call`` with ``login``'s GitHub token, refreshing once on a 401.

    Returns ``(token, result)`` — the token that actually worked, since callers
    reuse it for follow-up requests. A user access token can expire mid-session;
    forcing a refresh and retrying is the difference between a working dashboard
    and an unexplained re-login prompt.
    """
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    try:
        return token, await call(token)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        refreshed = await get_valid_access_token(login, force_refresh=True)
        if not refreshed:
            raise HTTPException(401, "github token expired, re-login required") from exc
        return refreshed, await call(refreshed)


async def assert_repo_access(full_name: str, token: str) -> str:
    full_name = normalize_repo_full_name(full_name)
    owner, name = full_name.split("/", 1)
    async with github_client(token=token) as client:
        response = await github_request(client, "GET", github_url(f"/repos/{owner}/{name}"))
    raise_for_github_status(response.status_code)
    return full_name


async def require_repo_access_for_user(login: str, full_name: str) -> str:
    token, _ = await with_user_github_token(login, lambda t: assert_repo_access(full_name, t))
    return token


async def repo_config_for_user(login: str, full_name: str | None) -> dict[str, str] | None:
    if not isinstance(full_name, str) or not full_name.strip():
        return None
    try:
        normalized = normalize_repo_full_name(full_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await require_repo_access_for_user(login, normalized)
    owner, name = normalized.split("/", 1)
    return {"owner": owner, "name": name}
