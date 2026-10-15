import asyncio
import base64
import gzip
import hashlib
import json
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import select

from openswe.dashboard import oauth as dashboard_oauth
from openswe.database import postgres
from openswe.mcp import hosted_oauth as hosted
from openswe.mcp import transport
from openswe.mcp.models import MCPConnection, MCPConnectionUpdate
from openswe.mcp.oauth import connection_auth
from openswe.mcp.routes import router
from openswe.mcp.user import get_user_mcp, save_user_mcp
from openswe.users.models import User


@dataclass
class OAuthServer:
    requests: list[httpx2.Request] = field(default_factory=list)
    refreshes: int = 0
    token_status: int = 200
    oversized: bool = False
    reject_tokens: bool = False
    metadata_issuer: str | None = None
    network_failure: Exception | None = None
    verifier: str = ""
    record: MCPConnection | None = None


@pytest.fixture
async def oauth_server(registry_db, monkeypatch):
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://openswe.example")
    await User.sign_in("github", "1", login="alice")
    await User.sign_in("github", "2", login="bob")
    state = OAuthServer()

    async def remote(request):
        host = request.headers["Host"]
        if state.oversized:
            return httpx2.Response(200, content=b"x" * (1024 * 1024 + 1))
        path = request.url.path
        state.requests.append(request)
        if host == "resource.example":
            if path.startswith("/.well-known/oauth-protected-resource"):
                assert "Authorization" not in request.headers
                assert "X-Private" not in request.headers
                return httpx2.Response(
                    200,
                    json={
                        "resource": "https://resource.example/mcp",
                        "authorization_servers": ["https://issuer.example"],
                    },
                )
            if (
                request.headers.get("Authorization", "").startswith("Bearer ")
                and not state.reject_tokens
            ):
                return httpx2.Response(200, json={"ok": True})
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://resource.example/.well-known/oauth-protected-resource/mcp"'
                },
            )
        assert host == "issuer.example"
        assert "X-Private" not in request.headers
        if path == "/.well-known/oauth-authorization-server":
            return httpx2.Response(
                200,
                headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
                content=gzip.compress(
                    json.dumps(
                        {
                            "issuer": (state.metadata_issuer or "https://issuer.example"),
                            "authorization_endpoint": "https://issuer.example/authorize",
                            "token_endpoint": "https://issuer.example/oauth/token",
                            "registration_endpoint": "https://issuer.example/register",
                            "response_types_supported": ["code"],
                            "grant_types_supported": ["authorization_code", "refresh_token"],
                            "token_endpoint_auth_methods_supported": ["none"],
                            "code_challenge_methods_supported": ["S256"],
                            "authorization_response_iss_parameter_supported": True,
                        }
                    ).encode()
                ),
            )
        if path == "/register":
            assert "Authorization" not in request.headers
            return httpx2.Response(
                201, json={**json.loads(request.content), "client_id": "hosted-client"}
            )
        assert path == "/oauth/token"
        if state.network_failure:
            raise state.network_failure
        fields = parse_qs(request.content.decode())
        if fields["grant_type"] == ["refresh_token"]:
            state.refreshes += 1
            assert fields["refresh_token"] == [f"refresh-secret-{state.refreshes}"]
            await asyncio.sleep(0.05)
        else:
            state.verifier = fields["code_verifier"][0]
        if state.token_status != 200:
            return httpx2.Response(
                state.token_status,
                json={"error": "invalid_grant", "error_description": "sensitive-secret"},
            )
        number = state.refreshes + 1
        return httpx2.Response(
            200,
            json={
                "access_token": f"access-secret-{number}",
                "token_type": "Bearer",
                "refresh_token": f"refresh-secret-{number}",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", lambda **kwargs: httpx2.MockTransport(remote))
    monkeypatch.setattr(
        transport, "resolve_and_validate", lambda url: (urlsplit(url).hostname, ["93.184.216.34"])
    )
    monkeypatch.setattr(
        hosted, "resolve_and_validate", lambda url: (urlsplit(url).hostname, ["93.184.216.34"])
    )
    await save_user_mcp(
        "alice",
        "notion",
        MCPConnectionUpdate(
            name="notion",
            url="https://resource.example/mcp",
            auth="oauth",
            headers={"X-Private": "custom-secret"},
        ),
    )
    record = await get_user_mcp("alice", "notion")
    assert record is not None
    state.record = record
    return state


async def connect(state):
    record = state.record
    url = await hosted.start_oauth("alice", record, "session-binding")
    fields = parse_qs(urlsplit(url).query)
    await hosted.complete_oauth(
        "alice",
        record,
        fields["state"][0],
        "callback-secret",
        "session-binding",
        "https://issuer.example",
    )
    return fields


async def test_split_hosted_callback_persists_encrypted_pkce_and_rejects_replays(oauth_server):
    record = oauth_server.record
    url = await hosted.start_oauth("alice", record, "session-binding")
    fields = parse_qs(urlsplit(url).query)
    stored = await hosted.AuthorizationStore("alice", "notion").get()
    assert stored is not None
    assert stored.tokens is None
    verifier = stored.verifier
    assert verifier
    assert fields["redirect_uri"] == [
        "https://openswe.example/dashboard/api/my-mcps/oauth/callback"
    ]
    for login, binding, issuer in [
        ("bob", "session-binding", "https://issuer.example"),
        ("alice", "wrong-session", "https://issuer.example"),
    ]:
        with pytest.raises(ValueError):
            await hosted.complete_oauth(
                login, record, fields["state"][0], "callback-secret", binding, issuer
            )
    await hosted.complete_oauth(
        "alice",
        record,
        fields["state"][0],
        "callback-secret",
        "session-binding",
        "https://issuer.example",
    )
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(oauth_server.verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert fields["code_challenge"] == [challenge]
    assert (await hosted.oauth_status("alice", record)).status == "connected"
    saved = await save_user_mcp(
        "alice",
        "notion",
        MCPConnectionUpdate(name="notion", url=record.url, auth="oauth", allowed_tools=["search"]),
    )
    assert saved["oauth_status"]["status"] == "connected"
    record = await get_user_mcp("alice", "notion")
    assert record is not None
    with pytest.raises(ValueError):
        await hosted.complete_oauth(
            "alice",
            record,
            fields["state"][0],
            "callback-secret",
            "session-binding",
            "https://issuer.example",
        )
    async with postgres.session() as session:
        encrypted = await session.scalar(select(hosted.PersonalMCPOAuthRow.sealed_authorization))
    assert encrypted is not None
    assert not any(
        secret in encrypted
        for secret in [
            "refresh-secret",
            "access-secret",
            "callback-secret",
            "hosted-client",
            verifier,
        ]
    )
    async with transport.mcp_http_client(
        record.url, auth=connection_auth(record, ("user_mcps", "alice"))
    ) as client:
        assert (await client.post(record.url)).status_code == 200
    assert oauth_server.refreshes == 0


async def test_refresh_is_cross_replica_rotating_and_restores_absolute_expiry(oauth_server):
    await connect(oauth_server)
    record = oauth_server.record
    store = hosted.AuthorizationStore("alice", record.name)
    saved = await store.get()
    saved.expires_at = time.time() - 10
    await store.put(saved)
    replicas = [hosted.PersonalOAuthAuth("alice", record) for _ in range(4)]
    assert (
        await asyncio.gather(*(auth.access_token() for auth in replicas)) == ["access-secret-2"] * 4
    )
    assert oauth_server.refreshes == 1
    saved = await store.get()
    assert saved.tokens.refresh_token == "refresh-secret-2"
    assert (saved.expires_at or 0) > time.time() + 3000
    assert await hosted.PersonalOAuthAuth("alice", record).access_token() == "access-secret-2"
    saved.expires_at = time.time() - 10
    await store.put(saved)
    assert await hosted.PersonalOAuthAuth("alice", record).access_token() == "access-secret-3"
    assert oauth_server.refreshes == 2


async def test_invalid_grant_and_missing_tokens_never_start_interactive_auth(oauth_server, caplog):
    await connect(oauth_server)
    record = oauth_server.record
    store = hosted.AuthorizationStore("alice", record.name)
    saved = await store.get()
    saved.expires_at = time.time() - 1
    await store.put(saved)
    oauth_server.token_status = 400
    for _ in range(2):
        with pytest.raises(ValueError, match="Reconnect"):
            await hosted.PersonalOAuthAuth("alice", record).access_token()
    assert oauth_server.refreshes == 1
    assert (await hosted.oauth_status("alice", record)).status == "reconnect_required"
    with pytest.raises(ValueError, match="Reconnect"):
        await hosted.PersonalOAuthAuth("bob", record).access_token()
    assert "sensitive-secret" not in caplog.text


@pytest.mark.parametrize("issuer", [None, "https://wrong.example"])
async def test_callback_issuer_validation_is_single_use(oauth_server, issuer):
    record = oauth_server.record
    url = await hosted.start_oauth("alice", record, "session-binding")
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(ValueError):
        await hosted.complete_oauth(
            "alice", record, state, "callback-secret", "session-binding", issuer
        )
    assert not any(request.url.path == "/oauth/token" for request in oauth_server.requests)
    with pytest.raises(ValueError):
        await hosted.complete_oauth(
            "alice", record, state, "callback-secret", "session-binding", "https://issuer.example"
        )


async def test_disconnect_during_refresh_does_not_resurrect_authorization(oauth_server):
    await connect(oauth_server)
    record = oauth_server.record
    store = hosted.AuthorizationStore("alice", record.name)
    saved = await store.get()
    await store.delete()
    with pytest.raises(ValueError, match="disconnected"):
        await store.finish(saved)
    assert await store.get() is None


async def test_discovery_rejects_issuer_mismatch_and_never_logs_remote_secrets(
    oauth_server, caplog
):
    oauth_server.metadata_issuer = "https://unrelated.example"
    with pytest.raises(ValueError, match="Could not start"):
        await hosted.start_oauth("alice", oauth_server.record, "session-binding")
    assert not any(request.url.path == "/register" for request in oauth_server.requests)
    assert "unrelated.example" not in caplog.text
    assert "custom-secret" not in caplog.text


async def test_all_oauth_requests_have_bounded_responses_and_private_destinations_are_blocked(
    oauth_server, monkeypatch
):
    provider = hosted.HostedProvider(
        oauth_server.record, hosted.Authorization.new(oauth_server.record)
    )
    oauth_server.oversized = True
    with pytest.raises(ValueError, match="size limit"):
        await provider.oauth_request(
            httpx2.Request("GET", "https://issuer.example/.well-known/oauth-authorization-server")
        )

    def reject(url):
        raise transport.UnsafeUrlError(url, "private")

    monkeypatch.setattr(transport, "resolve_and_validate", reject)
    count = len(oauth_server.requests)
    with pytest.raises(ValueError, match="public addresses"):
        await provider.oauth_request(httpx2.Request("GET", "https://private.example/metadata"))
    assert len(oauth_server.requests) == count


async def test_expired_state_and_changed_connection_are_rejected_before_exchange(oauth_server):
    record = oauth_server.record
    url = await hosted.start_oauth("alice", record, "session-binding")
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(ValueError):
        await hosted.complete_oauth(
            "alice",
            record.model_copy(update={"revision": "new"}),
            state,
            "callback-secret",
            "session-binding",
            "https://issuer.example",
        )
    store = hosted.AuthorizationStore("alice", record.name)
    saved = await store.get()
    saved.pending_until = time.time() - 1
    await store.put(saved)
    with pytest.raises(ValueError):
        await hosted.complete_oauth(
            "alice", record, state, "callback-secret", "session-binding", "https://issuer.example"
        )
    assert not any(request.url.path == "/oauth/token" for request in oauth_server.requests)


async def test_transient_provider_outage_is_failed_not_consent_revocation(oauth_server):
    await connect(oauth_server)
    record = oauth_server.record
    store = hosted.AuthorizationStore("alice", record.name)
    saved = await store.get()
    saved.expires_at = time.time() - 1
    await store.put(saved)
    oauth_server.token_status = 503
    with pytest.raises(ValueError, match="unavailable"):
        await hosted.PersonalOAuthAuth("alice", record).access_token()
    assert (await hosted.oauth_status("alice", record)).status == "failed"
    assert oauth_server.refreshes == 1
    saved = await store.get()
    assert saved.tokens.refresh_token == "refresh-secret-1"
    saved.retry_after = time.time() - 1
    await store.put(saved)
    oauth_server.token_status = 200
    oauth_server.refreshes = 0
    assert await hosted.PersonalOAuthAuth("alice", record).access_token() == "access-secret-2"


@pytest.mark.parametrize(
    "failure,preserved",
    [
        (httpx2.ConnectError("offline"), True),
        (httpx2.ConnectTimeout("offline"), True),
        (httpx2.ReadTimeout("unknown"), False),
    ],
)
async def test_only_ambiguous_refresh_failures_discard_rotating_credentials(
    oauth_server, failure, preserved
):
    await connect(oauth_server)
    record = oauth_server.record
    store = hosted.AuthorizationStore("alice", record.name)
    saved = await store.get()
    saved.expires_at = time.time() - 1
    await store.put(saved)
    oauth_server.network_failure = failure
    with pytest.raises(ValueError):
        await hosted.PersonalOAuthAuth("alice", record).access_token()
    saved = await store.get()
    assert (saved.tokens is not None) == preserved
    assert saved.status.status == ("failed" if preserved else "reconnect_required")


async def test_repeated_rejection_retries_once_then_requires_reconnect(oauth_server):
    await connect(oauth_server)
    record = oauth_server.record
    oauth_server.reject_tokens = True
    async with transport.mcp_http_client(
        record.url, auth=connection_auth(record, ("user_mcps", "alice"))
    ) as client:
        assert (await client.post(record.url, json={})).status_code == 401
    assert oauth_server.refreshes == 1
    assert (await hosted.oauth_status("alice", record)).status == "reconnect_required"


async def test_routes_bind_callback_to_signed_in_owner_and_session(oauth_server, monkeypatch):
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://openswe.example")
    app = FastAPI()
    app.include_router(router, prefix="/dashboard/api")
    session = {"sub": "alice"}
    app.dependency_overrides[dashboard_oauth.require_session] = lambda: session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://openswe.example",
        cookies={"osw_session": "browser-session"},
    ) as client:
        start = await client.post("/dashboard/api/my-mcps/notion/oauth/authorization")
        assert start.status_code == 200
        state = parse_qs(urlsplit(start.json()["authorization_url"]).query)["state"][0]
        assert (await client.get("/dashboard/api/my-mcps/notion/oauth")).json()[
            "status"
        ] == "connecting"
        session["sub"] = "bob"
        denied = await client.get(
            "/dashboard/api/my-mcps/oauth/callback",
            params={"state": state, "code": "callback-secret", "iss": "https://issuer.example"},
        )
        assert denied.status_code == 404
        session["sub"] = "alice"
        callback = await client.get(
            "/dashboard/api/my-mcps/oauth/callback",
            params={"state": state, "code": "callback-secret", "iss": "https://issuer.example"},
        )
        assert callback.status_code == 303
        assert (
            callback.headers["location"]
            == "https://openswe.example/my-settings/connections?mcp_oauth=connected"
        )
        assert callback.headers["referrer-policy"] == "no-referrer"
        assert (await client.get("/dashboard/api/my-mcps/notion/oauth")).json()[
            "status"
        ] == "connected"
        assert (await client.delete("/dashboard/api/my-mcps/notion/oauth")).status_code == 204
        assert (await client.get("/dashboard/api/my-mcps/notion/oauth")).json()[
            "status"
        ] == "not_connected"
