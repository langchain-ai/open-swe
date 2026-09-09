import json
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from mcp.types import Tool
from pydantic import ValidationError

from agent.dashboard import routes
from agent.dashboard import workspace_mcps as mcps
from agent.encryption import decrypt_token
from agent.tool_loaders import workspace_mcp as loader


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


async def test_generic_connection_roundtrip_redacts_and_preserves_headers(fake_store):
    update = mcps.WorkspaceMCPUpdate(
        name="incident",
        url="https://mcp.incident.io/mcp",
        headers={"Authorization": "Bearer test-secret"},
    )
    saved = await mcps.save_workspace_mcp("incident", update)
    assert saved["header_names"] == ["Authorization"]
    assert "test-secret" not in json.dumps(saved)
    raw = fake_store.values(["workspace_mcps"])["incident"]
    assert "test-secret" not in json.dumps(raw)
    assert json.loads(decrypt_token(raw["encrypted_headers"])) == {
        "Authorization": "Bearer test-secret"
    }
    revised = await mcps.save_workspace_mcp(
        "incident", mcps.WorkspaceMCPUpdate(name="incident", url=update.url, enabled=False)
    )
    assert revised["enabled"] is False
    assert revised["header_names"] == ["Authorization"]
    assert revised["revision"] != saved["revision"]
    assert await mcps.list_workspace_mcps() == [revised]
    await mcps.delete_workspace_mcp("incident")
    assert await mcps.list_workspace_mcps() == []


async def test_corrupt_record_does_not_hide_other_connections_or_log_values(fake_store, caplog):
    saved = await mcps.save_workspace_mcp(
        "example", mcps.WorkspaceMCPUpdate(name="example", url="https://example.com/mcp")
    )
    fake_store.values(["workspace_mcps"])["broken"] = {
        **saved,
        "name": "broken",
        "encrypted_headers": {"Authorization": "test-secret"},
    }

    assert await mcps.list_workspace_mcps() == [saved]
    with pytest.raises(ValidationError):
        await mcps.get_workspace_mcp("broken")
    assert "Skipping unreadable" in caplog.text
    assert "test-secret" not in caplog.text


async def test_url_change_requires_explicit_header_replacement(fake_store):
    await mcps.save_workspace_mcp(
        "example",
        mcps.WorkspaceMCPUpdate(
            name="example", url="https://one.example/mcp", headers={"Authorization": "secret"}
        ),
    )
    with pytest.raises(ValueError, match="headers"):
        await mcps.save_workspace_mcp(
            "example", mcps.WorkspaceMCPUpdate(name="example", url="https://two.example/mcp")
        )
    saved = await mcps.save_workspace_mcp(
        "example",
        mcps.WorkspaceMCPUpdate(name="example", url="https://two.example/mcp", headers={}),
    )
    assert saved["header_names"] == []


async def test_oauth_secret_is_encrypted_preserved_and_cleared(fake_store):
    oauth = {
        "token_url": "https://api.linear.app/oauth/token",
        "client_id": "test-app",
        "client_secret": "test-client-secret",
        "scope": "read,write",
    }
    saved = await mcps.save_workspace_mcp(
        "linear",
        mcps.WorkspaceMCPUpdate(name="linear", url="https://mcp.linear.app/mcp", oauth=oauth),
    )
    assert saved["oauth"]["client_id"] == "test-app"
    assert "client_secret" not in saved["oauth"]
    assert "test-client-secret" not in json.dumps(saved)
    raw = fake_store.values(["workspace_mcps"])["linear"]
    assert "test-client-secret" not in json.dumps(raw)
    assert decrypt_token(raw["encrypted_client_secret"]) == "test-client-secret"
    for fields in ({}, {"oauth": saved["oauth"]}):
        await mcps.save_workspace_mcp(
            "linear",
            mcps.WorkspaceMCPUpdate(name="linear", url=saved["url"], enabled=False, **fields),
        )
        assert (
            fake_store.values(["workspace_mcps"])["linear"]["encrypted_client_secret"]
            == raw["encrypted_client_secret"]
        )
    await mcps.save_workspace_mcp(
        "linear", mcps.WorkspaceMCPUpdate(name="linear", url=saved["url"], oauth=None)
    )
    assert fake_store.values(["workspace_mcps"])["linear"]["encrypted_client_secret"] == ""


async def test_new_oauth_connection_requires_secret(fake_store):
    with pytest.raises(ValueError, match="client secret"):
        await mcps.save_workspace_mcp(
            "linear",
            mcps.WorkspaceMCPUpdate(
                name="linear",
                url="https://mcp.linear.app/mcp",
                oauth={"token_url": "https://api.linear.app/oauth/token", "client_id": "app"},
            ),
        )
    assert await mcps.list_workspace_mcps() == []


@pytest.mark.parametrize("saved_header", [False, True])
async def test_oauth_rejects_explicit_or_saved_authorization_header(fake_store, saved_header):
    values = {"name": "linear", "url": "https://mcp.linear.app/mcp"}
    headers = {"authorization": "Bearer test-token"}
    if saved_header:
        await mcps.save_workspace_mcp("linear", mcps.WorkspaceMCPUpdate(**values, headers=headers))
    with pytest.raises(ValueError, match="Remove the Authorization header"):
        await mcps.save_workspace_mcp(
            "linear",
            mcps.WorkspaceMCPUpdate(
                **values,
                headers=None if saved_header else headers,
                oauth={
                    "token_url": "https://api.linear.app/oauth/token",
                    "client_id": "app",
                    "client_secret": "test-client-secret",
                },
            ),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://other.example/mcp"},
        {"token_url": "https://other.example/token"},
        {"client_id": "other-app"},
    ],
)
async def test_oauth_secret_cannot_be_reused_for_changed_destinations(fake_store, change):
    saved = await mcps.save_workspace_mcp(
        "linear",
        mcps.WorkspaceMCPUpdate(
            name="linear",
            url="https://mcp.linear.app/mcp",
            oauth={
                "token_url": "https://api.linear.app/oauth/token",
                "client_id": "test-app",
                "client_secret": "test-client-secret",
            },
        ),
    )
    with pytest.raises(ValueError, match="client secret"):
        await mcps.save_workspace_mcp(
            "linear",
            mcps.WorkspaceMCPUpdate(
                name="linear",
                url=change.get("url", saved["url"]),
                oauth={
                    **saved["oauth"],
                    **{key: value for key, value in change.items() if key != "url"},
                },
            ),
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"url": "http://example.com/mcp"},
        {"url": "https://user:password@example.com/mcp"},
        {"url": "https://example.com/mcp#fragment"},
        {"headers": {"Host": "metadata.internal"}},
        {"headers": {"Authorization": "secret\r\nHost: internal"}},
        {"headers": {"Authorization": "a", "authorization": "b"}},
        {"name": "invalid name"},
        {"transport": "stdio"},
        {
            "oauth": {
                "token_url": "http://example.com/token",
                "client_id": "app",
                "client_secret": "secret",
            }
        },
        {
            "oauth": {
                "token_url": "https://example.com/token?client_secret=secret",
                "client_id": "app",
            }
        },
        {"allowed_tools": [""]},
        {"allowed_tools": ["x" * 129]},
    ],
)
def test_invalid_connection_is_rejected(fields):
    values = {"name": "example", "url": "https://example.com/mcp", **fields}
    with pytest.raises(ValidationError):
        mcps.WorkspaceMCPUpdate(**values)


async def test_all_discovered_tools_can_be_saved_for_large_catalogs(fake_store, monkeypatch):
    tool_names = [f"tool_{index}" for index in range(201)]
    monkeypatch.setattr(
        loader,
        "_discover_tools",
        AsyncMock(return_value=[Tool(name=name, inputSchema={}) for name in tool_names]),
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._admin_session] = lambda: {"sub": "admin"}
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    body = {"name": "example", "url": "https://example.com/mcp"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        catalog = await client.post("/dashboard/api/workspace-mcps/example/discover", json=body)
        assert catalog.status_code == 200
        selected = [tool["name"] for tool in catalog.json()]
        assert selected == tool_names
        saved = await client.put(
            "/dashboard/api/workspace-mcps/example",
            json={**body, "allowed_tools": selected},
        )
        assert saved.status_code == 200
        assert saved.json()["allowed_tools"] == tool_names
        assert (await client.get("/dashboard/api/workspace-mcps")).json()[0][
            "allowed_tools"
        ] == tool_names


async def test_workspace_mcp_routes_are_admin_only_and_same_origin(fake_store, monkeypatch):
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    app = FastAPI()
    app.include_router(routes.router)
    session = {"sub": "admin", "email": "admin@example.com"}
    app.dependency_overrides[routes.require_session] = lambda: session
    body = {
        "name": "example",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "test-secret"},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.put("/dashboard/api/workspace-mcps/example", json=body)
        assert response.status_code == 200
        assert "test-secret" not in response.text
        assert len((await client.get("/dashboard/api/workspace-mcps")).json()) == 1
        session = {"sub": "member", "email": "member@example.com"}
        assert (await client.get("/dashboard/api/workspace-mcps")).status_code == 403
        assert (
            await client.put("/dashboard/api/workspace-mcps/example", json=body)
        ).status_code == 403
        assert (await client.delete("/dashboard/api/workspace-mcps/example")).status_code == 403
        session = {"sub": "admin", "email": "admin@example.com"}
        assert (
            await client.delete(
                "/dashboard/api/workspace-mcps/example", headers={"Origin": "http://attacker"}
            )
        ).status_code == 403
        assert (await client.delete("/dashboard/api/workspace-mcps/example")).status_code == 204


async def test_reveal_headers_requires_admin_and_same_origin_without_saving(
    fake_store, monkeypatch
):
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    saved = await mcps.save_workspace_mcp(
        "example",
        mcps.WorkspaceMCPUpdate(
            name="example", url="https://example.com/mcp", headers={"Authorization": "test-secret"}
        ),
    )
    app = FastAPI()
    app.include_router(routes.router)
    session = {"sub": "admin", "email": "admin@example.com"}
    app.dependency_overrides[routes.require_session] = lambda: session
    path = "/dashboard/api/workspace-mcps/example/headers/reveal"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.post(path)
        assert response.status_code == 200
        assert response.json() == {"Authorization": "test-secret"}
        assert response.headers["cache-control"] == "no-store"
        assert await mcps.list_workspace_mcps() == [saved]
        session = {"sub": "member", "email": "member@example.com"}
        denied = await client.post(path)
        assert denied.status_code == 403
        assert "test-secret" not in denied.text
        session = {"sub": "admin", "email": "admin@example.com"}
        denied = await client.post(path, headers={"Origin": "http://attacker"})
        assert denied.status_code == 403
        assert "test-secret" not in denied.text
        assert (
            await client.post("/dashboard/api/workspace-mcps/missing/headers/reveal")
        ).status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "test-secret\n"},
        {"Authorization": {"nested": "test-secret"}},
        ["test-secret"],
    ],
)
async def test_validation_responses_never_echo_headers(monkeypatch, headers):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._admin_session] = lambda: {"sub": "admin"}
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.put(
            "/dashboard/api/workspace-mcps/example",
            json={"name": "example", "url": "https://example.com/mcp", "headers": headers},
        )
    assert response.status_code == 422
    assert "test-secret" not in response.text


@pytest.mark.parametrize(
    "query_key",
    [
        "client_secret",
        "auth_token",
        "api-key",
        "x-api-key",
        "CLIENT_SECRET",
        "clientSecret",
        "AuthToken",
        "X-API-Key",
        "xApiKey",
        "api.key",
        "%63lient%5Fsecret",
        "accessToken",
        "refresh-token",
        "apiToken",
        "service_password",
    ],
)
async def test_query_credentials_are_rejected_without_saving_or_echoing(
    fake_store, monkeypatch, query_key
):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._admin_session] = lambda: {"sub": "admin"}
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.put(
            "/dashboard/api/workspace-mcps/example",
            json={
                "name": "example",
                "url": f"https://example.com/mcp?toolsets=core&{query_key}=test-secret",
            },
        )
        assert response.status_code == 422
        assert "test-secret" not in response.text
        assert fake_store.values(["workspace_mcps"]) == {}
        assert (await client.get("/dashboard/api/workspace-mcps")).json() == []


async def test_ordinary_query_parameters_roundtrip_unchanged(fake_store, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._admin_session] = lambda: {"sub": "admin"}
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    url = (
        "https://mcp.us5.datadoghq.com/v1/mcp"
        "?toolsets=core&region=us5&page_token=next&token_budget=1000&monkey=banana"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.put(
            "/dashboard/api/workspace-mcps/example", json={"name": "example", "url": url}
        )
        assert response.status_code == 200
        assert response.json()["url"] == url
        assert fake_store.values(["workspace_mcps"])["example"]["url"] == url
        assert (await client.get("/dashboard/api/workspace-mcps")).json()[0]["url"] == url


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"name": "incident.io"}, "Connection name"),
        ({"url": "http://example.com/mcp"}, "Server URL"),
        ({"transport": "stdio"}, "Transport"),
        ({"headers": {"Authorization": {"test-secret": "test-secret"}}}, "Headers"),
        (
            {
                "oauth": {
                    "token_url": "http://example.com/token",
                    "client_id": "app",
                    "client_secret": "test-secret",
                }
            },
            "OAuth",
        ),
    ],
)
async def test_validation_identifies_fields_without_echoing_input(monkeypatch, fields, message):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._admin_session] = lambda: {"sub": "admin"}
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.put(
            "/dashboard/api/workspace-mcps/example",
            json={
                "name": "example",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "test-secret"},
                **fields,
            },
        )
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert "test-secret" not in response.text


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("fails", [False, True])
async def test_discover_draft_never_saves_settings(fake_store, monkeypatch, existing, fails):
    previous = None
    if existing:
        previous = await mcps.save_workspace_mcp(
            "example",
            mcps.WorkspaceMCPUpdate(
                name="example", url="https://example.com/old", headers={"Authorization": "old"}
            ),
        )
    request = httpx.Request("POST", "https://example.com/mcp")
    error = httpx.HTTPStatusError(
        "test-secret", request=request, response=httpx.Response(403, request=request)
    )
    discover = AsyncMock(
        side_effect=ExceptionGroup("test-secret", [error]) if fails else None,
        return_value=[],
    )
    monkeypatch.setattr(loader, "_discover_tools", discover)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes._admin_session] = lambda: {"sub": "admin"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.post(
            "/dashboard/api/workspace-mcps/example/discover",
            json={
                "name": "example",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "test-secret"},
            },
        )
    assert response.status_code == (400 if fails else 200)
    if fails:
        assert "HTTP 403" in response.json()["detail"]
    assert "test-secret" not in response.text
    candidate = discover.call_args.args[0]
    assert candidate.url == "https://example.com/mcp"
    assert candidate.connection_headers() == {"Authorization": "test-secret"}
    current = await mcps.get_workspace_mcp("example")
    assert (current.public() if current else None) == previous
