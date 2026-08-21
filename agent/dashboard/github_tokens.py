"""The user's GitHub OAuth tokens, encrypted at rest.

They live in their own ``["oauth_tokens"]`` namespace, apart from the
user-editable profile in ``["profiles"]``: each write touches only its own
namespace, so an OAuth callback and a profile save can interleave without
clobbering each other's fields.
"""

from typing import Any

from ..encryption import encrypt_token
from ..store import now_iso
from .oauth import is_unrecoverable_refresh_error, refresh_user_access_token
from .token_vault import TokenFields, TokenVault, expires_at_from_response

OAUTH_TOKENS_NAMESPACE: list[str] = ["oauth_tokens"]

_FIELDS = TokenFields(access="encrypted_gh_token", refresh="encrypted_gh_refresh_token")


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
