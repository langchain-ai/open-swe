import json
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from mcp.types import Tool

from agent.dashboard import routes, workspace_mcps
from agent.dashboard import user_mcps as mcps
from agent.encryption import decrypt_token
from agent.mcp import MCPConnectionUpdate, load_mcp_tools, runtime


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def update(name="linear", **fields):
    return MCPConnectionUpdate(name=name, url="https://mcp.linear.app/mcp", **fields)


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    )


async def test_connections_are_stored_per_user_and_never_expose_secrets(fake_store):
    saved = await mcps.save_user_mcp(
        "alice", "linear", update(headers={"Authorization": "Bearer test-secret"})
    )
    assert saved["header_names"] == ["Authorization"]
    assert "test-secret" not in json.dumps(saved)
    raw = fake_store.values(["user_mcps", "alice"])["linear"]
    assert json.loads(decrypt_token(raw["encrypted_headers"])) == {
        "Authorization": "Bearer test-secret"
    }
    assert await mcps.list_user_mcps("alice") == [saved]
    assert await mcps.list_user_mcps("bob") == []
    assert await mcps.get_user_mcp("bob", "linear") is None
    assert await workspace_mcps.list_workspace_mcps() == []
    await mcps.delete_user_mcp("bob", "linear")
    assert await mcps.list_user_mcps("alice") == [saved]
    await mcps.delete_user_mcp("alice", "linear")
    assert await mcps.list_user_mcps("alice") == []


async def test_saved_credentials_are_reused_only_from_the_same_user(fake_store):
    await mcps.save_user_mcp("bob", "linear", update(headers={"Authorization": "bob-secret"}))
    await workspace_mcps.save_workspace_mcp(
        "linear", update(headers={"Authorization": "workspace-secret"})
    )
    saved = await mcps.save_user_mcp("alice", "linear", update(enabled=False))
    assert saved["header_names"] == []
    record = await mcps.get_user_mcp("alice", "linear")
    assert record is not None
    assert record.connection_headers() == {}


@pytest.mark.parametrize("login", ["", "   "])
def test_blank_login_cannot_address_a_store(login):
    with pytest.raises(ValueError):
        mcps.user_mcp_source(login)


async def test_personal_connection_replaces_workspace_connection_for_its_owner(
    fake_store, monkeypatch
):
    await workspace_mcps.save_workspace_mcp("linear", update(allowed_tools=["search", "delete"]))
    await mcps.save_user_mcp("alice", "linear", update(allowed_tools=["search"]))

    async def discover(record, namespace):
        return [Tool(name=name, inputSchema={"type": "object"}) for name in record.allowed_tools]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    workspace = workspace_mcps.workspace_mcp_source
    alice = await load_mcp_tools(workspace, mcps.user_mcp_source("alice"))
    bob = await load_mcp_tools(workspace, mcps.user_mcp_source("bob"))
    assert [tool.name for tool in alice] == [runtime._tool_name("linear", "search")]
    assert sorted(tool.name for tool in bob) == sorted(
        runtime._tool_name("linear", name) for name in ["search", "delete"]
    )


async def test_routes_serve_only_the_signed_in_users_connections(fake_store, monkeypatch):
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    app = FastAPI()
    app.include_router(routes.router)
    session = {"sub": "alice", "email": "alice@example.com"}
    app.dependency_overrides[routes.require_session] = lambda: session
    body = {
        "name": "linear",
        "url": "https://mcp.linear.app/mcp",
        "headers": {"Authorization": "test-secret"},
        "allowed_tools": ["search"],
    }
    async with client(app) as http:
        response = await http.put("/dashboard/api/my-mcps/linear", json=body)
        assert response.status_code == 200
        assert "test-secret" not in response.text
        assert response.json()["header_names"] == ["Authorization"]
        assert len((await http.get("/dashboard/api/my-mcps")).json()) == 1
        revealed = await http.post("/dashboard/api/my-mcps/linear/headers/reveal")
        assert revealed.status_code == 200
        assert revealed.json() == {"Authorization": "test-secret"}
        assert revealed.headers["cache-control"] == "no-store"
        assert (await http.get("/dashboard/api/workspace-mcps")).status_code == 403

        session = {"sub": "bob", "email": "bob@example.com"}
        assert (await http.get("/dashboard/api/my-mcps")).json() == []
        denied = await http.post("/dashboard/api/my-mcps/linear/headers/reveal")
        assert denied.status_code == 404
        assert "test-secret" not in denied.text
        assert (await http.delete("/dashboard/api/my-mcps/linear")).status_code == 204
        assert await mcps.list_user_mcps("alice") != []

        session = {"sub": "alice", "email": "alice@example.com"}
        assert (
            await http.delete(
                "/dashboard/api/my-mcps/linear", headers={"Origin": "http://attacker"}
            )
        ).status_code == 403
        invalid = await http.put(
            "/dashboard/api/my-mcps/linear",
            json={**body, "url": "http://insecure.example/mcp?token=test-secret"},
        )
        assert invalid.status_code == 422
        assert "test-secret" not in invalid.text
        assert "Server URL" in invalid.json()["detail"]
        assert (await http.delete("/dashboard/api/my-mcps/linear")).status_code == 204
        assert (await http.get("/dashboard/api/my-mcps")).json() == []


async def test_discover_uses_the_callers_draft_without_saving_or_borrowing_credentials(
    fake_store, monkeypatch
):
    await mcps.save_user_mcp("bob", "linear", update(headers={"Authorization": "bob-secret"}))
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(runtime, "_discover_tools", discover)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.require_session] = lambda: {"sub": "alice"}
    async with client(app) as http:
        response = await http.post(
            "/dashboard/api/my-mcps/linear/discover",
            json={"name": "linear", "url": "https://mcp.linear.app/mcp"},
        )
        assert response.status_code == 200
        assert response.json() == []
        missing = await http.post("/dashboard/api/my-mcps/other/discover")
        assert missing.status_code == 400
    candidate, namespace = discover.call_args.args
    assert namespace == ("user_mcps", "alice")
    assert candidate.connection_headers() == {}
    assert await mcps.list_user_mcps("alice") == []
