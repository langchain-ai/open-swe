from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException

from openswe.fleet_chat.routes import ASSISTANT_ID, OWNER_KEY, FleetChat, workspace_client

THREAD = UUID("018d50d4-3d52-7000-8000-000000000001")
WORKSPACE = UUID("018d50d4-3d52-7000-8000-000000000002")


@pytest.mark.parametrize(
    "owner,assistant,allowed",
    [("alice", ASSISTANT_ID, True), ("bob", ASSISTANT_ID, False), ("alice", "other", False)],
)
async def test_thread_access_requires_owner_and_chat_agent(owner, assistant, allowed):
    async def upstream(request):
        return httpx.Response(
            200,
            json={
                "thread_id": str(THREAD),
                "metadata": {OWNER_KEY: owner, "assistant_id": assistant},
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.smith.langchain.com", transport=httpx.MockTransport(upstream)
    ) as http:
        client = FleetChat("alice", http)
        if allowed:
            assert (await client.thread(THREAD))["thread_id"] == str(THREAD)
        else:
            with pytest.raises(HTTPException) as error:
                await client.thread(THREAD)
            assert error.value.status_code == 404


@pytest.mark.parametrize("available", [True, False])
async def test_workspace_membership_is_checked_before_selecting_tenant(available):
    async def upstream(request):
        return httpx.Response(
            200, json=[{"id": str(WORKSPACE), "data_plane_url": None}] if available else []
        )

    async with httpx.AsyncClient(
        base_url="https://api.smith.langchain.com", transport=httpx.MockTransport(upstream)
    ) as http:
        client = FleetChat("alice", http)
        if available:
            await workspace_client(WORKSPACE, client)
            assert http.headers["X-Tenant-Id"] == str(WORKSPACE)
        else:
            with pytest.raises(HTTPException) as error:
                await workspace_client(WORKSPACE, client)
            assert error.value.status_code == 403
            assert "X-Tenant-Id" not in http.headers


async def test_upstream_errors_do_not_expose_response_body():
    async def upstream(request):
        return httpx.Response(403, text="private credential details")

    async with httpx.AsyncClient(
        base_url="https://api.smith.langchain.com", transport=httpx.MockTransport(upstream)
    ) as http:
        with pytest.raises(HTTPException) as error:
            await FleetChat("alice", http).request("GET", "/workspaces")
        assert error.value.status_code == 403
        assert "private credential" not in error.value.detail
