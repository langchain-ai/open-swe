"""Resumable, encrypted MCP OAuth authorization-code flows with PKCE and refresh."""

import asyncio
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import quote_plus, urlencode

import httpx
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import AnyHttpUrl, ValidationError

from agent.dashboard.mcp_connections import _get, _lock, _put, _seal, _unseal, discover_connection
from agent.dashboard.mcp_http import (
    MCPConnectionError,
    request_json,
    resolve_url,
    safe_client,
    validate_url,
)
from agent.encryption import decrypt_token, encrypt_token
from agent.store import delete_value, get_value, put_value

_FLOW_NAMESPACE = "mcp_oauth_flows"
_FLOW_TTL = 600


async def _metadata(urls: list[str]) -> dict[str, Any]:
    async with asyncio.timeout(30), safe_client(timeout=httpx.Timeout(20)) as client:
        for url in urls:
            async with client.stream(
                "GET", url, headers={"Accept": "application/json"}
            ) as response:
                if response.status_code in {404, 405}:
                    continue
                if not response.is_success:
                    raise MCPConnectionError(502, "MCP OAuth metadata discovery failed")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_048_576:
                        raise MCPConnectionError(502, "OAuth response exceeds the size limit")
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError
                return data
    raise MCPConnectionError(404, "MCP OAuth metadata discovery is unavailable")


async def _discover(url: str, authorization_server: str = "") -> tuple[dict[str, Any], str, str]:
    try:
        if authorization_server:
            await resolve_url(authorization_server)
            authorization_server = str(AnyHttpUrl(authorization_server))
        async with safe_client(timeout=httpx.Timeout(20)) as client:
            async with client.stream(
                "GET", url, headers={"Accept": "application/json, text/event-stream"}
            ) as response:
                metadata_url = extract_resource_metadata_from_www_auth(response)
                scope = extract_scope_from_www_auth(response)
        if metadata_url:
            await resolve_url(metadata_url)
        try:
            resource_metadata = await _metadata(
                build_protected_resource_metadata_discovery_urls(metadata_url, url)
            )
        except MCPConnectionError as exc:
            if not authorization_server or exc.status_code != 404:
                raise
            resource_metadata = None
        prm = (
            ProtectedResourceMetadata.model_validate(resource_metadata)
            if resource_metadata is not None
            else None
        )
        resource = str(prm.resource) if prm else resource_url_from_server_url(url)
        if not check_resource_allowed(
            requested_resource=resource_url_from_server_url(url), configured_resource=resource
        ):
            raise MCPConnectionError(502, "OAuth resource does not match the MCP endpoint")
        await resolve_url(resource)
        servers = [str(server) for server in prm.authorization_servers or []] if prm else []
        if authorization_server and servers and authorization_server not in servers:
            raise MCPConnectionError(
                502, "OAuth authorization server conflicts with resource metadata"
            )
        issuer = authorization_server or (servers[0] if servers else "")
        if not issuer:
            raise MCPConnectionError(502, "MCP OAuth authorization server is missing")
        await resolve_url(issuer)
        metadata = OAuthMetadata.model_validate(
            await _metadata(build_oauth_authorization_server_metadata_discovery_urls(issuer, url))
        )
        if str(metadata.issuer) != issuer:
            raise MCPConnectionError(502, "OAuth issuer does not match discovery metadata")
        if "S256" not in (metadata.code_challenge_methods_supported or []):
            raise MCPConnectionError(502, "OAuth server must support S256 PKCE")
        for endpoint in (
            metadata.authorization_endpoint,
            metadata.token_endpoint,
            metadata.registration_endpoint,
        ):
            if endpoint:
                await resolve_url(str(endpoint))
        return (
            metadata.model_dump(mode="json"),
            resource,
            scope or (" ".join(prm.scopes_supported or []) if prm else ""),
        )
    except httpx.HTTPError, ValidationError, ValueError, TimeoutError:
        raise MCPConnectionError(502, "Invalid MCP OAuth discovery response") from None


async def _client(
    record: dict[str, Any], metadata: dict[str, Any], redirect_uri: str, scope: str
) -> dict[str, Any]:
    client_metadata = {
        "client_name": "Open SWE",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": record.get("oauth_token_endpoint_auth_method", "none"),
        "scope": scope,
    }
    methods = metadata.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    method = client_metadata["token_endpoint_auth_method"]
    if method not in methods:
        raise MCPConnectionError(
            409, "Choose an OAuth client authentication method supported by the server"
        )
    if record.get("oauth_client_id"):
        client_info = {
            **client_metadata,
            "client_id": record["oauth_client_id"],
            "client_secret": record.get("oauth_client_secret") or None,
        }
    else:
        endpoint = metadata.get("registration_endpoint")
        if not endpoint:
            raise MCPConnectionError(409, "This server requires a manually registered OAuth client")
        client_info = await request_json("POST", endpoint, json=client_metadata)
    try:
        info = OAuthClientInformationFull.model_validate(client_info)
        if not info.client_id or info.token_endpoint_auth_method not in {
            "none",
            "client_secret_basic",
            "client_secret_post",
        }:
            raise ValueError
        if info.token_endpoint_auth_method != "none" and not info.client_secret:
            raise ValueError
        if redirect_uri not in [str(uri) for uri in info.redirect_uris or []]:
            raise ValueError
        return info.model_dump(mode="json")
    except ValidationError, ValueError:
        raise MCPConnectionError(502, "Invalid OAuth client registration") from None


async def start_oauth(login: str, id: str, redirect_uri: str) -> str:
    validate_url(redirect_uri)
    async with _lock(login, id):
        record = await _get(login, id)
        if record["auth_type"] != "oauth":
            raise MCPConnectionError(409, "Select OAuth authentication first")
        metadata, resource, suggested_scope = await _discover(
            record["url"], record.get("oauth_authorization_server", "")
        )
        scope = record.get("oauth_scope") or suggested_scope
        client = await _client(record, metadata, redirect_uri, scope)
        pkce = PKCEParameters.generate()
        nonce = secrets.token_urlsafe(32)
        state = encrypt_token(
            json.dumps({"owner": login, "nonce": nonce, "expires_at": time.time() + _FLOW_TTL})
        )
        flow_key = hashlib.sha256(state.encode()).hexdigest()
        flow = {
            "owner": login,
            "id": id,
            "revision": record["revision"],
            "expires_at": time.time() + _FLOW_TTL,
            "redirect_uri": redirect_uri,
            "verifier": pkce.code_verifier,
            "metadata": metadata,
            "resource": resource,
            "client": client,
        }
        await put_value([_FLOW_NAMESPACE, login], flow_key, _seal(flow))
        return f"{metadata['authorization_endpoint']}?{
            urlencode(
                {
                    'response_type': 'code',
                    'client_id': client['client_id'],
                    'redirect_uri': redirect_uri,
                    'state': state,
                    'code_challenge': pkce.code_challenge,
                    'code_challenge_method': 'S256',
                    'resource': resource,
                    'scope': scope,
                }
            )
        }"


async def _token(oauth: dict[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    client = oauth["client"]
    data = {"client_id": client["client_id"], "resource": oauth["resource"], **fields}
    headers = {"Accept": "application/json"}
    method = client.get("token_endpoint_auth_method", "none")
    if method == "client_secret_post":
        data["client_secret"] = client["client_secret"]
    elif method == "client_secret_basic":
        auth = httpx.BasicAuth(
            quote_plus(client["client_id"], safe=""), quote_plus(client["client_secret"], safe="")
        )
        headers["Authorization"] = next(
            auth.auth_flow(httpx.Request("POST", "https://oauth.invalid"))
        ).headers["Authorization"]
    result = await request_json(
        "POST", oauth["metadata"]["token_endpoint"], data=data, headers=headers
    )
    try:
        tokens = OAuthToken.model_validate(result)
        if (
            tokens.token_type.lower() != "bearer"
            or not tokens.access_token
            or any(ord(c) < 33 or ord(c) > 126 for c in tokens.access_token)
        ):
            raise ValueError
        return tokens.model_dump(mode="json", exclude_none=True)
    except ValidationError, ValueError:
        raise MCPConnectionError(502, "Invalid OAuth token response") from None


def _store_tokens(oauth: dict[str, Any], tokens: dict[str, Any]) -> None:
    previous_refresh = oauth.get("tokens", {}).get("refresh_token")
    if "refresh_token" not in tokens and previous_refresh:
        tokens["refresh_token"] = previous_refresh
    oauth["tokens"] = tokens
    oauth["expires_at"] = time.time() + tokens["expires_in"] if "expires_in" in tokens else None


async def finish_oauth(state: str, code: str) -> dict[str, Any]:
    try:
        if (
            not isinstance(state, str)
            or len(state) > 8192
            or not isinstance(code, str)
            or not code
            or len(code) > 8192
        ):
            raise ValueError
        state_data = json.loads(decrypt_token(state))
        login = state_data["owner"]
        if state_data["expires_at"] < time.time() or not isinstance(login, str):
            raise ValueError
    except ValueError, TypeError, KeyError, AttributeError:
        raise MCPConnectionError(400, "OAuth state is invalid or expired") from None
    flow_key = hashlib.sha256(state.encode()).hexdigest()
    namespace = [_FLOW_NAMESPACE, login]
    async with _lock(login, flow_key):
        stored = await get_value(namespace, flow_key)
        if stored is None:
            raise MCPConnectionError(400, "OAuth state is invalid or expired")
        flow = _unseal(stored)
        await delete_value(namespace, flow_key)
        if flow["owner"] != login or flow["expires_at"] < time.time():
            raise MCPConnectionError(400, "OAuth state is invalid or expired")
        async with _lock(login, flow["id"]):
            record = await _get(login, flow["id"])
            if record["revision"] != flow["revision"] or record["auth_type"] != "oauth":
                raise MCPConnectionError(409, "MCP connection changed; restart OAuth")
            oauth = {key: flow[key] for key in ("metadata", "resource", "client")}
            tokens = await _token(
                oauth,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": flow["redirect_uri"],
                    "code_verifier": flow["verifier"],
                },
            )
            _store_tokens(oauth, tokens)
            record["oauth"] = oauth
            await _put(login, record)
    return await discover_connection(login, flow["id"])


async def access_token(login: str, record: dict[str, Any]) -> str:
    async with _lock(login, record["id"]):
        current = await _get(login, record["id"])
        if not current["enabled"] or current["revision"] != record["revision"]:
            raise MCPConnectionError(409, "MCP connection changed; reconnect")
        return await _access_token_locked(login, current)


async def _access_token_locked(login: str, record: dict[str, Any]) -> str:
    oauth = record.get("oauth", {})
    if oauth.get("refresh_pending"):
        raise MCPConnectionError(409, "MCP OAuth refresh interrupted; reconnect")
    tokens = oauth.get("tokens", {})
    if not tokens.get("access_token"):
        raise MCPConnectionError(409, "MCP OAuth authorization required")
    expires_at = oauth.get("expires_at")
    if expires_at is not None and expires_at <= time.time() + 60:
        if not tokens.get("refresh_token"):
            raise MCPConnectionError(409, "MCP OAuth authorization expired; reconnect")
        oauth["refresh_pending"] = True
        await _put(login, record)
        refreshed = await _token(
            oauth, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
        )
        oauth.pop("refresh_pending")
        _store_tokens(oauth, refreshed)
        await _put(login, record)
    return oauth["tokens"]["access_token"]
