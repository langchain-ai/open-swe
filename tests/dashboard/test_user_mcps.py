import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI

from agent.dashboard import routes, workspace_mcps
from agent.dashboard import user_mcps as mcps
from agent.mcp import MCPConnectionUpdate, load_mcp_tools


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def update(**fields):
    return MCPConnectionUpdate(name="linear", url="https://mcp.linear.app/mcp", **fields)


async def test_saved_credentials_are_reused_only_from_the_same_user(fake_store):
    await mcps.save_user_mcp("bob", "linear", update(headers={"Authorization": "bob-secret"}))
    await workspace_mcps.save_workspace_mcp(
        "linear", update(headers={"Authorization": "workspace-secret"}, allowed_tools=["search"])
    )
    saved = await mcps.save_user_mcp(" Alice ", "linear", update(enabled=False))
    assert saved["header_names"] == []
    assert fake_store.values(["user_mcps", "alice"])["linear"]["encrypted_headers"] == ""
    assert await mcps.list_user_mcps("bob") != [saved]
    assert await mcps.list_user_mcps("ALICE") == [saved]
    assert (
        await load_mcp_tools(workspace_mcps.workspace_mcp_source, mcps.user_mcp_source("alice"))
        == []
    )
    record = await mcps.user_mcp_source("ALICE").get_connection("linear")
    assert record is not None and not record.enabled
    await mcps.delete_user_mcp("ALICE", "linear")
    assert await mcps.list_user_mcps("alice") == []


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
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        response = await client.put("/dashboard/api/my-mcps/linear", json=body)
        assert response.status_code == 200
        assert "test-secret" not in response.text
        assert len((await client.get("/dashboard/api/my-mcps")).json()) == 1
        revealed = await client.post("/dashboard/api/my-mcps/linear/headers/reveal")
        assert revealed.json() == {"Authorization": "test-secret"}
        assert revealed.headers["cache-control"] == "no-store"

        session = {"sub": "bob", "email": "bob@example.com"}
        assert (await client.get("/dashboard/api/my-mcps")).json() == []
        denied = await client.post("/dashboard/api/my-mcps/linear/headers/reveal")
        assert denied.status_code == 404
        assert "test-secret" not in denied.text
        assert (await client.delete("/dashboard/api/my-mcps/linear")).status_code == 204
        assert await mcps.list_user_mcps("alice") != []

        session = {"sub": "alice", "email": "alice@example.com"}
        assert (await client.delete("/dashboard/api/my-mcps/linear")).status_code == 204
        assert await mcps.list_user_mcps("alice") == []
