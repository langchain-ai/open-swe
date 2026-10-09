"""A person's own LangSmith connection (Sign in with LangSmith), for any feature that
calls LangSmith as them. ``langsmith_access_token`` is the entry point for callers.

LangSmith is an OAuth 2.1 authorization server. Open SWE uses a confidential client,
with PKCE, registered in the LangSmith organization (self-registered clients can only reach
LangSmith's MCP resource, not its API), keeps each person's tokens encrypted in the
``user_oauth_credential`` table and refreshes them on demand.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlparse

import httpx2
from pydantic import BaseModel, ValidationError

from openswe.config import ENV
from openswe.dashboard.oauth import pkce_s256_challenge
from openswe.dashboard.oauth_credentials import (
    OAuthProvider,
    delete_credential,
    load_credential,
    save_credential,
)
from openswe.dashboard.oauth_refresh import refresh_guard
from openswe.database import postgres
from openswe.encryption import decrypt_token, encrypt_token
from openswe.store import now_iso
from openswe.users.records import UnknownUser, UserRecords

logger = logging.getLogger(__name__)

LANGSMITH_KEY: OAuthProvider = "langsmith"
LANGSMITH_STATE_COOKIE_NAME = "osw_langsmith_oauth_state"
LANGSMITH_OAUTH_FLOWS = UserRecords("langsmith_oauth_flow")
_METADATA_PATH = "/.well-known/oauth-authorization-server"
_HTTP_TIMEOUT = httpx2.Timeout(15.0, connect=5.0)
_EXPIRY_SKEW = timedelta(minutes=2)


class LangSmithOAuthError(Exception):
    def __init__(self, status_code: int, detail: str, *, error_code: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code


def langsmith_issuer() -> str:
    return ENV.LANGSMITH_CONNECTION_URL.get().rstrip("/")


def _same_origin(url: str) -> str:
    issuer = urlparse(langsmith_issuer())
    parsed = urlparse(url)
    if (parsed.scheme, parsed.netloc) != (issuer.scheme, issuer.netloc):
        raise LangSmithOAuthError(502, "LangSmith OAuth metadata points at another origin")
    return url


def _error(response: httpx2.Response, fallback: str) -> LangSmithOAuthError:
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        description = data.get("error_description")
        detail = description if isinstance(description, str) else data["error"]
        return LangSmithOAuthError(
            response.status_code, f"{fallback}: {detail}", error_code=data["error"]
        )
    return LangSmithOAuthError(response.status_code, fallback)


async def _post(
    url: str,
    *,
    fallback: str,
    json: dict[str, object] | None = None,
    data: dict[str, str] | None = None,
) -> dict[str, object]:
    try:
        async with httpx2.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                url, headers={"Accept": "application/json"}, json=json, data=data
            )
    except httpx2.RequestError as exc:
        raise LangSmithOAuthError(503, f"{fallback}: network error") from exc
    if not response.is_success:
        raise _error(response, fallback)
    data = response.json()
    if not isinstance(data, dict):
        raise LangSmithOAuthError(502, f"{fallback}: invalid response")
    return data


async def _metadata() -> dict[str, str]:
    try:
        async with httpx2.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(langsmith_issuer() + _METADATA_PATH)
    except httpx2.RequestError as exc:
        raise LangSmithOAuthError(503, "LangSmith OAuth discovery failed") from exc
    if not response.is_success:
        raise _error(response, "LangSmith OAuth discovery failed")
    data = response.json()
    endpoints: dict[str, str] = {}
    for key in ("authorization_endpoint", "token_endpoint"):
        value = data.get(key) if isinstance(data, dict) else None
        if not isinstance(value, str) or not value:
            raise LangSmithOAuthError(502, f"LangSmith OAuth discovery missing {key}")
        endpoints[key] = _same_origin(value)
    return endpoints


def langsmith_oauth_configured() -> bool:
    """Credentials live in PostgreSQL, so the connection needs it alongside the client."""
    return (
        ENV.LANGSMITH_OAUTH_CLIENT_ID.is_set()
        and ENV.LANGSMITH_OAUTH_CLIENT_SECRET.is_set()
        and postgres.configured()
    )


def _client_id() -> str:
    """An org-registered client: LangSmith limits self-registered (DCR) ones to its MCP resource."""
    client_id = ENV.LANGSMITH_OAUTH_CLIENT_ID.optional()
    if not client_id or not langsmith_oauth_configured():
        raise LangSmithOAuthError(503, "Sign in with LangSmith is not configured")
    return client_id


def _client_secret() -> str:
    """Confidential client: a leaked code or refresh token is useless without this secret."""
    secret = ENV.LANGSMITH_OAUTH_CLIENT_SECRET.optional()
    if not secret:
        raise LangSmithOAuthError(503, "Sign in with LangSmith is not configured")
    return secret


async def start_langsmith_oauth(
    login: str, nonce_hash: str, *, redirect_uri: str, state: str
) -> str:
    """Store a pending flow and return LangSmith's authorize URL."""
    endpoints = await _metadata()
    client_id = _client_id()
    verifier = secrets.token_urlsafe(32)
    await LANGSMITH_OAUTH_FLOWS.put(
        login,
        {
            "encrypted_code_verifier": encrypt_token(verifier),
            "client_id": client_id,
            "token_endpoint": endpoints["token_endpoint"],
            "redirect_uri": redirect_uri,
            "created_at": now_iso(),
        },
        nonce_hash,
    )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce_s256_challenge(verifier),
        "code_challenge_method": "S256",
        "scope": "openid email offline_access",
        "resource": langsmith_issuer(),
    }
    return f"{endpoints['authorization_endpoint']}?{urlencode(params)}"


def _expires_at(data: dict[str, object]) -> datetime | None:
    seconds = data.get("expires_in")
    if not isinstance(seconds, int | float) or seconds <= 0:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(seconds))


async def _email(access_token: str) -> str | None:
    """The connected account's email, for display; a failed lookup does not block connecting."""
    try:
        async with httpx2.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(
                langsmith_issuer() + "/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        data = response.json() if response.is_success else None
    except httpx2.RequestError, ValueError:
        logger.warning("LangSmith userinfo lookup failed", exc_info=True)
        return None
    email = data.get("email") if isinstance(data, dict) else None
    return email if isinstance(email, str) else None


async def _save_tokens(
    login: str,
    data: dict[str, object],
    *,
    client_id: str,
    token_endpoint: str,
    new_grant: bool = False,
) -> None:
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise LangSmithOAuthError(502, "LangSmith OAuth returned no access token")
    refresh_token = data.get("refresh_token")
    previous = None if new_grant else await load_credential(LANGSMITH_KEY, login)
    # A new sign-in may be a different LangSmith account; a refresh keeps the same one.
    email = (previous.account_email if previous else None) or await _email(access_token)
    try:
        await save_credential(
            LANGSMITH_KEY,
            login,
            encrypted_access_token=encrypt_token(access_token),
            encrypted_refresh_token=(
                encrypt_token(refresh_token)
                if isinstance(refresh_token, str) and refresh_token
                else previous.encrypted_refresh_token
                if previous
                else None
            ),
            access_token_expires_at=_expires_at(data),
            client_id=client_id,
            token_endpoint=token_endpoint,
            account_email=email,
        )
    except UnknownUser:
        raise LangSmithOAuthError(400, "Sign in to Open SWE again, then reconnect") from None


async def complete_langsmith_oauth(login: str, nonce_hash: str, code: str) -> None:
    flow = await LANGSMITH_OAUTH_FLOWS.pop(login, nonce_hash)
    if not isinstance(flow, dict):
        raise LangSmithOAuthError(400, "oauth flow expired — please retry")
    verifier = decrypt_token(flow.get("encrypted_code_verifier", ""))
    if not verifier:
        raise LangSmithOAuthError(400, "stored LangSmith OAuth flow is incomplete")
    token_endpoint = _same_origin(str(flow["token_endpoint"]))
    data = await _post(
        token_endpoint,
        fallback="LangSmith OAuth token exchange failed",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": flow["client_id"],
            "client_secret": _client_secret(),
            "redirect_uri": flow["redirect_uri"],
            "code_verifier": verifier,
            "resource": langsmith_issuer(),
        },
    )
    await _save_tokens(
        login,
        data,
        client_id=str(flow["client_id"]),
        token_endpoint=token_endpoint,
        new_grant=True,
    )


async def langsmith_status(login: str) -> dict[str, object]:
    available = langsmith_oauth_configured()
    credential = await load_credential(LANGSMITH_KEY, login) if available else None
    return {
        "available": available,
        "connected": credential is not None,
        "email": credential.account_email if credential else None,
        "updated_at": (
            credential.updated_at.isoformat() if credential and credential.updated_at else None
        ),
    }


async def disconnect_langsmith(login: str) -> None:
    cached = await LANGSMITH_SANDBOX_KEYS.pop(login)
    if cached is not None:
        try:
            await _revoke_sandbox_key(login, SandboxKey.model_validate(cached))
        except LangSmithOAuthError, ValidationError:
            logger.warning("Could not revoke LangSmith sandbox key", exc_info=True)
    await delete_credential(LANGSMITH_KEY, login)


def _expired(expires_at: datetime | None) -> bool:
    return expires_at is not None and datetime.now(UTC) + _EXPIRY_SKEW >= expires_at


async def langsmith_access_token(login: str) -> str | None:
    """The user's current LangSmith access token, refreshed when near expiry; None if unlinked."""
    if not langsmith_oauth_configured():
        return None
    credential = await load_credential(LANGSMITH_KEY, login)
    if credential is None:
        return None
    if not _expired(credential.access_token_expires_at):
        return decrypt_token(credential.encrypted_access_token) or None
    async with refresh_guard(LANGSMITH_KEY, login):
        credential = await load_credential(LANGSMITH_KEY, login)
        if credential is None:
            return None
        if not _expired(credential.access_token_expires_at):
            return decrypt_token(credential.encrypted_access_token) or None
        refresh_token = decrypt_token(credential.encrypted_refresh_token or "")
        if not refresh_token:
            await delete_credential(LANGSMITH_KEY, login)
            logger.info("LangSmith token expired without a refresh token", extra={"login": login})
            return None
        token_endpoint = _same_origin(credential.token_endpoint)
        try:
            data = await _post(
                token_endpoint,
                fallback="LangSmith OAuth token refresh failed",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": credential.client_id,
                    "client_secret": _client_secret(),
                    "resource": langsmith_issuer(),
                },
            )
        except LangSmithOAuthError as exc:
            if exc.error_code != "invalid_grant":
                raise
            # The connect callback does not take the guard; keep a grant it wrote meanwhile.
            latest = await load_credential(LANGSMITH_KEY, login)
            if latest and latest.encrypted_refresh_token != credential.encrypted_refresh_token:
                return decrypt_token(latest.encrypted_access_token) or None
            await delete_credential(LANGSMITH_KEY, login)
            logger.info("LangSmith grant revoked; user must reconnect", extra={"login": login})
            return None
        await _save_tokens(
            login, data, client_id=credential.client_id, token_endpoint=token_endpoint
        )
        return str(data["access_token"])


LANGSMITH_SANDBOX_KEYS = UserRecords("langsmith_sandbox_key")
_PAT_PATH = "/api/v1/orgs/current/personal-access-tokens"
_SANDBOX_KEY_TTL = timedelta(hours=3)
# Longer than the sandbox proxy's hourly refresh, so an injected key never lapses mid-run.
_SANDBOX_KEY_MIN_REMAINING = timedelta(minutes=70)


class SandboxKey(BaseModel):
    """A personal access token minted as the user for CLIs that only take API keys."""

    id: str
    encrypted_key: str
    expires_at: datetime

    @property
    def fresh(self) -> bool:
        return self.expires_at - datetime.now(UTC) > _SANDBOX_KEY_MIN_REMAINING


async def _pat_request(
    access_token: str, method: str, path: str, *, json: dict[str, object] | None = None
) -> httpx2.Response:
    try:
        async with httpx2.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.request(
                method,
                langsmith_issuer() + path,
                headers={"Authorization": f"Bearer {access_token}"},
                json=json,
            )
    except httpx2.RequestError as exc:
        raise LangSmithOAuthError(503, "LangSmith API key request failed: network error") from exc
    if not response.is_success:
        raise _error(response, "LangSmith API key request failed")
    return response


async def _revoke_sandbox_key(login: str, key: SandboxKey) -> None:
    access_token = await langsmith_access_token(login)
    if access_token is not None:
        await _pat_request(access_token, "DELETE", f"{_PAT_PATH}/{key.id}")


async def langsmith_sandbox_api_key(login: str) -> str | None:
    """A short-lived API key acting as the user, reused until near expiry; None if unlinked."""
    if not langsmith_oauth_configured():
        return None
    access_token = await langsmith_access_token(login)
    if access_token is None:
        await LANGSMITH_SANDBOX_KEYS.delete(login)
        return None
    cached = await LANGSMITH_SANDBOX_KEYS.get(login)
    if cached is not None:
        try:
            key = SandboxKey.model_validate(cached)
        except ValidationError:
            logger.warning("Discarding malformed LangSmith sandbox key", exc_info=True)
        else:
            if key.fresh and (secret := decrypt_token(key.encrypted_key)):
                return secret
    expires_at = datetime.now(UTC) + _SANDBOX_KEY_TTL
    response = await _pat_request(
        access_token,
        "POST",
        _PAT_PATH,
        json={"description": "Open SWE sandbox", "expires_at": expires_at.isoformat()},
    )
    data = response.json()
    secret = data.get("key") if isinstance(data, dict) else None
    pat_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(secret, str) or not secret or not isinstance(pat_id, str):
        raise LangSmithOAuthError(502, "LangSmith returned no API key")
    key = SandboxKey(id=pat_id, encrypted_key=encrypt_token(secret), expires_at=expires_at)
    await LANGSMITH_SANDBOX_KEYS.put(login, key.model_dump(mode="json"))
    return secret
