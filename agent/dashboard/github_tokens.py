"""The user's GitHub OAuth tokens: minted, refreshed, and encrypted at rest.

They live in their own ``["oauth_tokens"]`` namespace, apart from the
user-editable profile in ``["profiles"]``: each write touches only its own
namespace, so an OAuth callback and a profile save can interleave without
clobbering each other's fields.
"""

from typing import Any

import httpx

from ..config import github_app_oauth
from ..encryption import encrypt_token
from ..store import now_iso
from ..utils.http import DEFAULT_HTTP_TIMEOUT
from .token_vault import TokenFields, TokenVault, expires_at_from_response

OAUTH_TOKENS_NAMESPACE: list[str] = ["oauth_tokens"]

_FIELDS = TokenFields(access="encrypted_gh_token", refresh="encrypted_gh_refresh_token")

GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"


class GithubOAuthError(Exception):
    """A GitHub OAuth token endpoint error, carrying GitHub's ``error`` code.

    ``status_code``/``detail`` are the HTTP answer the web layer should give;
    it maps them itself so this module stays free of FastAPI.
    """

    def __init__(self, status_code: int, detail: str, *, error_code: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code


# Error codes GitHub returns when a refresh token can never mint a new access
# token again (the user must re-authorize). Anything else is treated as
# transient so we don't needlessly drop a usable authorization.
UNRECOVERABLE_REFRESH_ERROR_CODES = frozenset({"bad_refresh_token", "unauthorized_client"})


def is_unrecoverable_refresh_error(exc: BaseException) -> bool:
    """Whether ``exc`` means the stored refresh token is permanently dead."""
    return (
        isinstance(exc, GithubOAuthError)
        and (exc.error_code or "") in UNRECOVERABLE_REFRESH_ERROR_CODES
    )


async def _request_github_tokens(body: dict[str, str]) -> dict[str, Any]:
    client_id, client_secret = github_app_oauth()
    if not client_id or not client_secret:
        raise GithubOAuthError(500, "GitHub App OAuth not configured")
    async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT) as client:
        resp = await client.post(
            GITHUB_OAUTH_TOKEN_URL,
            headers={"Accept": "application/json"},
            data=body,
        )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise GithubOAuthError(502, "unexpected GitHub OAuth response")
    if data.get("error"):
        raise GithubOAuthError(
            400,
            f"github oauth error: {data.get('error_description') or data['error']}",
            error_code=str(data["error"]),
        )
    return data


async def exchange_code(code: str) -> dict[str, Any]:
    """Exchange an OAuth authorization code for user-to-server tokens."""
    client_id, client_secret = github_app_oauth()
    data = await _request_github_tokens(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
        }
    )
    if not data.get("access_token"):
        raise GithubOAuthError(400, f"oauth exchange failed: {data}")
    return data


async def refresh_user_access_token(refresh_token: str) -> dict[str, Any]:
    """Rotate an expiring user access token using its refresh token."""
    client_id, client_secret = github_app_oauth()
    data = await _request_github_tokens(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )
    if not data.get("access_token"):
        raise GithubOAuthError(400, f"oauth refresh failed: {data}")
    return data


def _token_record(
    existing: dict[str, Any],
    *,
    login: str,
    email: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Build the record to store from a GitHub code-exchange or refresh response."""
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("GitHub OAuth response missing access_token")
    record: dict[str, Any] = {
        "login": login,
        "email": email or existing.get("email", ""),
        _FIELDS.access: encrypt_token(access_token),
        "updated_at": now_iso(),
    }
    refresh_token = data.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        record[_FIELDS.refresh] = encrypt_token(refresh_token)
    elif existing.get(_FIELDS.refresh):
        record[_FIELDS.refresh] = existing[_FIELDS.refresh]
    token_expires_at = expires_at_from_response(data)
    if token_expires_at:
        record[_FIELDS.expires_at] = token_expires_at
    refresh_token_expires_at = expires_at_from_response(data, field="refresh_token_expires_in")
    if refresh_token_expires_at:
        record["refresh_token_expires_at"] = refresh_token_expires_at
    return record


async def _refresh_github_tokens(
    *, login: str, refresh_token: str, record: dict[str, Any]
) -> dict[str, Any]:
    data = await refresh_user_access_token(refresh_token)
    email = record.get("email")
    return _token_record(
        record,
        login=login,
        email=email if isinstance(email, str) else "",
        data=data,
    )


_vault = TokenVault(
    "GitHub",
    locate=lambda login: (OAUTH_TOKENS_NAMESPACE, login),
    fields=_FIELDS,
    refresh=_refresh_github_tokens,
    is_permanently_dead=is_unrecoverable_refresh_error,
)


async def upsert_access_token_from_github_response(
    login: str, email: str, data: dict[str, Any]
) -> None:
    """Store the tokens from a GitHub OAuth code exchange or refresh response."""
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return
    existing = await _vault.get_record(login) or {}
    await _vault.store_record(login, _token_record(existing, login=login, email=email, data=data))


async def get_oauth_token_record(login: str) -> dict[str, Any] | None:
    """The raw encrypted-token record, for callers that need its expiry metadata."""
    return await _vault.get_record(login)


async def get_valid_access_token(login: str, *, force_refresh: bool = False) -> str | None:
    """A GitHub access token for ``login``, refreshed when it is near expiry."""
    record = await _vault.get_valid(login, force_refresh=force_refresh)
    return _vault.access_token(record) if record else None


async def has_access_token_record(login: str) -> bool:
    """Whether an OAuth token record exists for ``login``.

    Distinguishes "user has never completed a GitHub login" (no record) from
    "the stored authorization is present but no longer usable" (record exists
    but won't decrypt / was revoked), so callers can prompt accurately.
    """
    return bool(await _vault.get_record(login))
