"""Notion MCP OAuth helpers."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from langgraph_sdk import get_client

from ..encryption import decrypt_token, encrypt_token

NOTION_MCP_URL = "https://mcp.notion.com/mcp"
NOTION_STATE_COOKIE_NAME = "osw_notion_oauth_state"
NOTION_OAUTH_FLOW_NAMESPACE: list[str] = ["notion_oauth_flows"]

_NOTION_HOST = "mcp.notion.com"
_PROTECTED_RESOURCE_METADATA_URL = "https://mcp.notion.com/.well-known/oauth-protected-resource"
_AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class NotionOAuthError(Exception):
    """Notion OAuth endpoint error."""

    def __init__(self, status_code: int, detail: str, *, error_code: str | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code


def _client():
    return get_client()


def _is_notion_https_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == _NOTION_HOST


def _require_notion_https_url(url: str, label: str) -> str:
    if not _is_notion_https_url(url):
        raise NotionOAuthError(502, f"invalid Notion OAuth {label}")
    return url


def _metadata_url(auth_server_url: str) -> str:
    parsed = urlparse(_require_notion_https_url(auth_server_url, "authorization server"))
    return f"{parsed.scheme}://{parsed.netloc}{_AUTHORIZATION_SERVER_METADATA_PATH}"


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_code_verifier() -> str:
    return _base64url(secrets.token_bytes(32))


def code_challenge_for_verifier(verifier: str) -> str:
    return _base64url(hashlib.sha256(verifier.encode()).digest())


def build_notion_authorize_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
) -> str:
    _require_notion_https_url(authorization_endpoint, "authorization endpoint")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "consent",
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


async def discover_notion_oauth_metadata() -> dict[str, Any]:
    """Discover Notion MCP OAuth endpoints."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            protected_resource = await client.get(_PROTECTED_RESOURCE_METADATA_URL)
            if not protected_resource.is_success:
                raise _oauth_error_from_response(
                    protected_resource,
                    "Notion OAuth protected resource discovery failed",
                )
            resource_metadata = protected_resource.json()
            auth_servers = resource_metadata.get("authorization_servers")
            if not isinstance(auth_servers, list) or not auth_servers:
                raise NotionOAuthError(
                    502, "Notion OAuth discovery returned no authorization server"
                )
            auth_server = auth_servers[0]
            if not isinstance(auth_server, str):
                raise NotionOAuthError(
                    502,
                    "Notion OAuth discovery returned invalid authorization server",
                )

            metadata_response = await client.get(_metadata_url(auth_server))
            if not metadata_response.is_success:
                raise _oauth_error_from_response(
                    metadata_response,
                    "Notion OAuth authorization server discovery failed",
                )
            metadata = metadata_response.json()
    except httpx.RequestError as exc:
        raise NotionOAuthError(503, "Notion OAuth discovery failed") from exc

    if not isinstance(metadata, dict):
        raise NotionOAuthError(502, "Notion OAuth discovery returned invalid metadata")
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        value = metadata.get(key)
        if not isinstance(value, str) or not value:
            raise NotionOAuthError(502, f"Notion OAuth discovery missing {key}")
        _require_notion_https_url(value, key)
    return metadata


async def register_notion_oauth_client(
    metadata: dict[str, Any],
    *,
    redirect_uri: str,
) -> dict[str, Any]:
    """Register this deployment as a Notion MCP OAuth client."""
    registration_endpoint = metadata.get("registration_endpoint")
    if not isinstance(registration_endpoint, str):
        raise NotionOAuthError(502, "Notion OAuth metadata missing registration endpoint")
    _require_notion_https_url(registration_endpoint, "registration endpoint")
    body: dict[str, Any] = {
        "client_name": os.environ.get("NOTION_MCP_CLIENT_NAME", "Open SWE"),
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    client_uri = os.environ.get("DASHBOARD_BASE_URL", "").strip()
    if client_uri:
        body["client_uri"] = client_uri

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                registration_endpoint,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.RequestError as exc:
        raise NotionOAuthError(503, "Notion OAuth client registration failed") from exc
    if not response.is_success:
        raise _oauth_error_from_response(response, "Notion OAuth client registration failed")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("client_id"), str):
        raise NotionOAuthError(502, "Notion OAuth client registration missing client_id")
    return data


async def store_notion_oauth_flow(
    login: str,
    nonce_hash: str,
    *,
    redirect_uri: str,
    state: str,
) -> str:
    """Create and store a short-lived Notion OAuth flow."""
    metadata = await discover_notion_oauth_metadata()
    client_info = await register_notion_oauth_client(metadata, redirect_uri=redirect_uri)
    verifier = generate_code_verifier()
    authorization_endpoint = str(metadata["authorization_endpoint"])
    client_id = str(client_info["client_id"])
    client_secret = (
        client_info.get("client_secret")
        if isinstance(client_info.get("client_secret"), str)
        else None
    )
    value = {
        "login": login,
        "encrypted_code_verifier": encrypt_token(verifier),
        "client_id": client_id,
        "encrypted_client_secret": encrypt_token(client_secret) if client_secret else None,
        "token_endpoint": str(metadata["token_endpoint"]),
        "redirect_uri": redirect_uri,
        "created_at": datetime.now(UTC).isoformat(),
    }
    await _client().store.put_item([*NOTION_OAUTH_FLOW_NAMESPACE, login], nonce_hash, value)
    return build_notion_authorize_url(
        authorization_endpoint=authorization_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge_for_verifier(verifier),
        state=state,
    )


async def pop_notion_oauth_flow(login: str, nonce_hash: str) -> dict[str, Any] | None:
    """Read and delete a pending Notion OAuth flow."""
    namespace = [*NOTION_OAUTH_FLOW_NAMESPACE, login]
    try:
        item = await _client().store.get_item(namespace, nonce_hash)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    try:
        await _client().store.delete_item(namespace, nonce_hash)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict):
        return None
    encrypted_code_verifier = value.pop("encrypted_code_verifier", "")
    if encrypted_code_verifier:
        value["code_verifier"] = decrypt_token(encrypted_code_verifier)
    encrypted_client_secret = value.pop("encrypted_client_secret", "")
    if encrypted_client_secret:
        value["client_secret"] = decrypt_token(encrypted_client_secret) or None
    return value


def _oauth_error_from_response(response: httpx.Response, fallback: str) -> NotionOAuthError:
    error_code = None
    detail = fallback
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        raw_error = data.get("error")
        if isinstance(raw_error, str):
            error_code = raw_error
            raw_description = data.get("error_description")
            description = raw_description if isinstance(raw_description, str) else raw_error
            detail = f"{fallback}: {description}"
    elif response.text:
        detail = f"{fallback}: {response.text[:200]}"
    return NotionOAuthError(response.status_code, detail, error_code=error_code)


async def exchange_notion_code(code: str, flow: dict[str, Any]) -> dict[str, Any]:
    """Exchange a Notion OAuth code for tokens."""
    token_endpoint = flow.get("token_endpoint")
    client_id = flow.get("client_id")
    redirect_uri = flow.get("redirect_uri")
    code_verifier = flow.get("code_verifier")
    if not all(
        isinstance(v, str) and v for v in (token_endpoint, client_id, redirect_uri, code_verifier)
    ):
        raise NotionOAuthError(400, "stored Notion OAuth flow is incomplete")
    _require_notion_https_url(token_endpoint, "token endpoint")
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    client_secret = flow.get("client_secret")
    if isinstance(client_secret, str) and client_secret:
        body["client_secret"] = client_secret
    return await _request_token(token_endpoint, body, fallback="Notion OAuth token exchange failed")


async def refresh_notion_access_token(
    *,
    refresh_token: str,
    token_endpoint: str,
    client_id: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    """Refresh a Notion OAuth access token."""
    _require_notion_https_url(token_endpoint, "token endpoint")
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        body["client_secret"] = client_secret
    return await _request_token(token_endpoint, body, fallback="Notion OAuth token refresh failed")


async def _request_token(
    token_endpoint: str, body: dict[str, str], *, fallback: str
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                token_endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "OpenSWE-Notion-MCP/1.0",
                },
                data=body,
            )
    except httpx.RequestError as exc:
        raise NotionOAuthError(503, f"{fallback}: network error") from exc
    if not response.is_success:
        raise _oauth_error_from_response(response, fallback)
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
        raise NotionOAuthError(502, f"{fallback}: missing access_token")
    return data


def is_reauth_required_error(exc: BaseException) -> bool:
    return isinstance(exc, NotionOAuthError) and exc.error_code == "invalid_grant"
