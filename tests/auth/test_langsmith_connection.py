import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid7

import httpx
import jwt
import langgraph_sdk
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from jwt.algorithms import OKPAlgorithm
from pydantic import SecretStr

from agent.dashboard import oauth as dashboard_oauth
from agent.dashboard.routes import router
from agent.encryption import decrypt_token
from agent.langsmith_connection import credentials, oauth, tools
from tests.conftest import FakeStore


@pytest.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore, registry_db: None
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "langsmith-test-secret")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://dashboard.example")
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="https://dashboard.example",
        headers={"origin": "https://dashboard.example"},
    ) as result:
        result.cookies.set(
            dashboard_oauth.COOKIE_NAME,
            dashboard_oauth.issue_session(
                login="alice", email=None, avatar_url=None, user_id=str(uuid7())
            ),
        )
        yield result


@pytest.mark.asyncio
async def test_key_validation_preserves_existing_connection_and_redacts_errors(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, fake_store: FakeStore
) -> None:
    from mcp.types import Tool

    monkeypatch.setattr(
        credentials,
        "discover_tools",
        AsyncMock(return_value=[Tool(name="search", inputSchema={"type": "object"})]),
    )
    accepted = AsyncMock(return_value=httpx.Response(200, json={}))
    monkeypatch.setattr(credentials, "request", accepted)
    response = await client.put(
        "/dashboard/api/my-credentials/langsmith", json={"api_key": "private-key", "region": "eu"}
    )
    assert response.status_code == 200
    assert response.json()["connected"]
    assert "private-key" not in response.text
    record = await credentials.store("ALICE").get("langsmith")
    assert record is not None
    assert decrypt_token(record.encrypted_token) == "private-key"
    assert "private-key" not in str(fake_store.items)
    assert await credentials.store("bob").get("langsmith") is None

    accepted.side_effect = oauth.ConnectionError(
        "LangSmith rejected the credential", status_code=400
    )
    rejected = await client.put(
        "/dashboard/api/my-credentials/langsmith", json={"api_key": "bad-key"}
    )
    assert rejected.status_code == 400
    assert await credentials.store("alice").get("langsmith") == record
    invalid = await client.put(
        "/dashboard/api/my-credentials/langsmith",
        json={"api_key": {"private-key": "secret"}, "workspace_id": "secret-workspace"},
    )
    assert invalid.status_code == 422
    assert "secret" not in invalid.text and "private-key" not in invalid.text
    accepted.reset_mock()
    rejected_region = await client.put(
        "/dashboard/api/my-credentials/langsmith",
        json={"api_key": "private-key", "region": "https://attacker.example"},
    )
    assert rejected_region.status_code == 422
    accepted.assert_not_awaited()
    assert await credentials.store("alice").get("langsmith") == record
    csrf = await client.delete(
        "/dashboard/api/my-credentials/langsmith", headers={"origin": "https://evil.example"}
    )
    assert csrf.status_code == 403
    assert await credentials.store("alice").get("langsmith") == record


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_key_validation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def validate(*args: object, **kwargs: object) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json={})

    monkeypatch.setattr(credentials, "request", validate)
    monkeypatch.setattr(credentials, "discover_tools", AsyncMock(return_value=[]))
    saving = asyncio.create_task(
        client.put("/dashboard/api/my-credentials/langsmith", json={"api_key": "pending-key"})
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert (await client.delete("/dashboard/api/my-credentials/langsmith")).status_code == 200
    release.set()
    assert (await saving).status_code == 409
    assert not (await client.get("/dashboard/api/my-credentials/langsmith")).json()["connected"]
    assert await credentials.load("alice") is None


@pytest.mark.asyncio
async def test_oauth_callback_is_bound_to_browser_and_validates_signed_identity(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = Ed25519PrivateKey.generate()
    jwk = OKPAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk["kid"] = "test"
    state = ""
    nonce = ""

    async def upstream(
        region: oauth.Region,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        body: dict[str, str | list[str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": oauth.issuer(region),
                    "authorization_endpoint": oauth.issuer(region) + "/oauth/authorize",
                    "token_endpoint": oauth.issuer(region) + "/oauth/token",
                    "registration_endpoint": oauth.issuer(region) + "/oauth/register",
                },
            )
        if path == "/oauth/register":
            return httpx.Response(200, json={"client_id": "test-client"})
        if path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [jwk]})
        assert data is not None and data["resource"] == oauth.issuer(region) + "/mcp"
        assert data["code_verifier"]
        token = jwt.encode(
            {
                "iss": oauth.issuer(region),
                "aud": "test-client",
                "sub": "ls-alice",
                "email": "alice@example.com",
                "nonce": nonce,
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=10),
            },
            key,
            algorithm="EdDSA",
            headers={"kid": "test"},
        )
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "id_token": token,
            },
        )

    monkeypatch.setattr(oauth, "request", upstream)
    monkeypatch.setattr(credentials, "discover_tools", AsyncMock(return_value=[]))
    started = await client.get("/dashboard/api/langsmith/login?region=eu")
    assert started.status_code == 302
    query = parse_qs(urlparse(started.headers["location"]).query)
    state, nonce = query["state"][0], query["nonce"][0]
    assert query["resource"] == [oauth.issuer(oauth.Region.EU) + "/mcp"]
    params = {"state": state, "code": "provider-code"}
    cookie = client.cookies.get("osw_langsmith_oauth_state")
    assert cookie is not None
    client.cookies.delete("osw_langsmith_oauth_state")
    assert (await client.get("/dashboard/api/langsmith/callback", params=params)).status_code == 400
    client.cookies.set("osw_langsmith_oauth_state", cookie)
    completed = await client.get("/dashboard/api/langsmith/callback", params=params)
    assert completed.status_code == 302
    status = (await client.get("/dashboard/api/my-credentials/langsmith")).json()
    assert status["email"] == "alice@example.com" and status["method"] == "oauth"
    assert await credentials.store("bob").get("langsmith") is None
    assert (await client.get("/dashboard/api/langsmith/callback", params=params)).status_code == 400
    wrong_identity = jwt.encode(
        {
            "iss": "https://wrong.example",
            "aud": "test-client",
            "sub": "alice",
            "nonce": nonce,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=10),
        },
        key,
        algorithm="EdDSA",
        headers={"kid": "test"},
    )
    with pytest.raises(oauth.ConnectionError, match="invalid identity"):
        await oauth.verify_identity(
            oauth.Region.EU, SecretStr(wrong_identity), client_id="test-client", nonce=nonce
        )

    verifier, challenge = oauth.verifier_and_challenge()
    started = await client.get(
        "/dashboard/api/langsmith/login",
        params={"desktop_handoff": challenge, "desktop_port": 51234},
    )
    query = parse_qs(urlparse(started.headers["location"]).query)
    state, nonce = query["state"][0], query["nonce"][0]
    client.cookies.clear()
    completed = await client.get(
        "/dashboard/api/langsmith/callback", params={"state": state, "code": "desktop-code"}
    )
    assert completed.status_code == 302
    location = urlparse(completed.headers["location"])
    assert location.netloc == "127.0.0.1:51234"
    handoff = parse_qs(location.query)["code"][0]
    client.cookies.set(
        dashboard_oauth.COOKIE_NAME,
        dashboard_oauth.issue_session(
            login="alice", email=None, avatar_url=None, user_id=str(uuid7())
        ),
    )
    exchange = await client.post(
        "/dashboard/api/langsmith/desktop/exchange",
        json={"code": handoff, "verifier": "wrong-verifier"},
    )
    assert exchange.status_code == 400
    exchange = await client.post(
        "/dashboard/api/langsmith/desktop/exchange", json={"code": handoff, "verifier": verifier}
    )
    assert exchange.status_code == 200 and exchange.json()["connected"]
    assert await credentials.store("bob").get("langsmith") is None
    replay = await client.post(
        "/dashboard/api/langsmith/desktop/exchange", json={"code": handoff, "verifier": verifier}
    )
    assert replay.status_code == 400
    started = await client.get("/dashboard/api/langsmith/login")
    query = parse_qs(urlparse(started.headers["location"]).query)
    state, nonce = query["state"][0], query["nonce"][0]
    assert (await client.delete("/dashboard/api/my-credentials/langsmith")).status_code == 200
    cancelled = await client.get(
        "/dashboard/api/langsmith/callback", params={"state": state, "code": "cancelled-code"}
    )
    assert cancelled.status_code == 409
    assert not (await client.get("/dashboard/api/my-credentials/langsmith")).json()["connected"]


@pytest.mark.asyncio
async def test_refresh_rotation_and_disconnect_cannot_restore_credentials(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = credentials.oauth_record(
        oauth.TokenResponse(
            access_token=SecretStr("old"), refresh_token=SecretStr("refresh"), expires_in=3600
        ),
        oauth.Region.US,
        "client",
        oauth.Identity(sub="alice"),
    )
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    record.allowed_tools = ["search"]
    await credentials.store("alice").put("langsmith", record)
    entered, release = asyncio.Event(), asyncio.Event()

    async def refresh(region: oauth.Region, data: dict[str, str]) -> oauth.TokenResponse:
        entered.set()
        await release.wait()
        return oauth.TokenResponse(
            access_token=SecretStr("new"), refresh_token=SecretStr("rotated"), expires_in=3600
        )

    refreshing = AsyncMock(side_effect=refresh)
    monkeypatch.setattr(credentials, "token_request", refreshing)
    loading = asyncio.create_task(credentials.load("alice"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    competing = asyncio.create_task(credentials.load("alice"))
    await asyncio.sleep(0.2)
    release.set()
    connection, concurrent_connection = await asyncio.gather(loading, competing)
    assert connection is not None and concurrent_connection is not None
    assert connection.connection_headers()["Authorization"] == "Bearer new"
    assert concurrent_connection.connection_headers()["Authorization"] == "Bearer new"
    refreshing.assert_awaited_once()
    rotated = await credentials.store("alice").get("langsmith")
    assert rotated is not None and decrypt_token(rotated.encrypted_refresh_token) == "rotated"
    with monkeypatch.context() as fast_path:
        fast_path.setattr(
            credentials, "lock", AsyncMock(side_effect=AssertionError("valid tokens must not lock"))
        )
        assert await credentials.load("alice") is not None
    await credentials.store("alice").put("langsmith", record)
    entered.clear()
    release.clear()
    loading = asyncio.create_task(credentials.load("alice"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await asyncio.wait_for(credentials.disconnect("alice"), timeout=2)
    assert not (await credentials.status("alice")).connected
    release.set()
    assert await loading is None
    assert await credentials.load("alice") is None
    await credentials.store("alice").put("langsmith", record)
    monkeypatch.setattr(
        credentials,
        "token_request",
        AsyncMock(side_effect=oauth.ConnectionError("unavailable", status_code=503)),
    )
    with pytest.raises(oauth.ConnectionError):
        await credentials.load("alice")
    assert not (await credentials.status("alice")).reconnect_required
    monkeypatch.setattr(
        credentials,
        "token_request",
        AsyncMock(side_effect=oauth.ConnectionError("revoked", invalid_grant=True)),
    )
    assert await credentials.load("alice") is None
    assert (await credentials.status("alice")).reconnect_required


@pytest.mark.asyncio
async def test_langsmith_source_rechecks_private_owner_on_each_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {"visibility": "private", "owner_type": "user", "owner_login": "alice"}
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"thread_id": "thread", "github_login": "alice"}},
    )
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _id: {"metadata": metadata}))
        ),
    )
    record = AsyncMock(return_value=None)
    monkeypatch.setattr(tools, "load", record)
    source = tools.source("alice")
    await source.get_connection("personal_langsmith")
    record.assert_awaited_once_with("alice")
    record.reset_mock()
    metadata["visibility"] = "public"
    assert await source.get_connection("personal_langsmith") is None
    record.assert_not_awaited()
    metadata["visibility"] = "private"
    metadata["owner_login"] = "bob"
    with pytest.raises(RuntimeError, match="private thread owner"):
        await source.get_connection("personal_langsmith")
    record.assert_not_awaited()
