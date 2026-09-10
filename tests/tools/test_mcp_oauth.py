import asyncio
import base64
import json
import socket
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet

from agent.dashboard import workspace_mcps as settings
from agent.mcp import MCPConnectionUpdate, runtime
from agent.mcp import oauth as mcp_oauth
from agent.mcp import transport as mcp_transport
from agent.tool_loaders import workspace_mcp as loader


@pytest.fixture
def oauth_remote(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    state: dict[str, Any] = {"tokens": [], "calls": [], "reject": False, "token_status": 200}

    async def remote(request):
        if request.headers["host"] == "auth.example":
            state["tokens"].append(request)
            await asyncio.sleep(0)
            return httpx.Response(
                state["token_status"],
                json=state.get(
                    "token_payload",
                    {
                        "access_token": f"test-token-{len(state['tokens'])}",
                        "token_type": "Bearer",
                        "expires_in": 120,
                        "error_description": "test-client-secret",
                    },
                ),
            )
        assert "test-client-secret" not in str(request.headers)
        assert "test-client-secret" not in request.content.decode()
        state["calls"].append(request.headers.get("Authorization"))
        accepted = (
            request.headers.get("Authorization") == f"Bearer test-token-{len(state['tokens'])}"
        )
        if accepted and not state["reject"] and "reply" in state:
            return state["reply"](request)
        return httpx.Response(200 if accepted and not state["reject"] else 401, json={})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(remote))
    monkeypatch.setattr(
        mcp_transport,
        "resolve_and_validate",
        lambda url: (
            True,
            "",
            urlsplit(url).hostname,
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        ),
    )
    return state


async def oauth_record(method="client_secret_post"):
    return await settings.prepare_workspace_mcp(
        "linear",
        MCPConnectionUpdate.model_validate(
            {
                "name": "linear",
                "url": "https://mcp.example/mcp",
                "oauth": {
                    "token_url": "https://auth.example/token",
                    "client_id": "test-app",
                    "client_secret": "test-client-secret",
                    "scope": "read,write",
                    "token_endpoint_auth_method": method,
                },
            }
        ),
    )


def client_for(record, namespace=("workspace_mcps",)):
    connection = runtime._connection(record, namespace)
    factory = connection["httpx_client_factory"]
    assert factory is not None
    return factory(headers=connection["headers"], auth=connection.get("auth"))


@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic"])
async def test_oauth_authenticates_and_reuses_token_across_connections(
    fake_store, oauth_remote, method
):
    record = await oauth_record(method)
    for _ in range(2):
        async with client_for(record) as client:
            responses = await asyncio.gather(*(client.post(record.url, json={}) for _ in range(3)))
            assert all(response.status_code == 200 for response in responses)
    assert len(oauth_remote["tokens"]) == 1
    request = oauth_remote["tokens"][0]
    body = parse_qs(request.content.decode())
    assert body["grant_type"] == ["client_credentials"]
    assert body["scope"] == ["read,write"]
    if method == "client_secret_post":
        assert body["client_id"] == ["test-app"]
        assert body["client_secret"] == ["test-client-secret"]
    else:
        assert "client_secret" not in body
        assert (
            base64.b64decode(request.headers["Authorization"].split()[1])
            == b"test-app:test-client-secret"
        )


async def test_identical_oauth_credentials_do_not_share_tokens_between_owners(
    fake_store, oauth_remote
):
    record = await oauth_record()
    for owner in ["alice", "bob"]:
        async with client_for(record, ("user_mcps", owner)) as client:
            assert (await client.post(record.url, json={})).status_code == 200
    assert len(oauth_remote["tokens"]) == 2


async def test_oauth_retries_unauthorized_only_once(fake_store, oauth_remote):
    record = await oauth_record()
    async with client_for(record) as client:
        assert (await client.post(record.url, json={})).status_code == 200
        oauth_remote["reject"] = True
        assert (await client.post(record.url, json={})).status_code == 401
    assert oauth_remote["calls"] == [
        "Bearer test-token-1",
        "Bearer test-token-1",
        "Bearer test-token-2",
    ]
    assert len(oauth_remote["tokens"]) == 2


async def test_oauth_failure_redacts_remote_response(fake_store, oauth_remote, caplog):
    record = await oauth_record()
    oauth_remote["token_status"] = 400
    async with client_for(record) as client:
        with pytest.raises(ValueError, match="OAuth") as error:
            await client.post(record.url, json={})
    assert "test-client-secret" not in str(error.value)
    assert "test-client-secret" not in caplog.text
    assert oauth_remote["calls"] == []


async def test_oauth_refreshes_expiring_tokens_and_rotated_secrets(
    fake_store, oauth_remote, monkeypatch
):
    now = 100
    monkeypatch.setattr(mcp_oauth, "monotonic", lambda: now)
    record = await oauth_record()
    async with client_for(record) as client:
        assert (await client.post(record.url, json={})).status_code == 200
        now = 215
        assert (await client.post(record.url, json={})).status_code == 200
    assert len(oauth_remote["tokens"]) == 2
    rotated = await oauth_record()
    async with client_for(rotated) as client:
        assert (await client.post(record.url, json={})).status_code == 200
    assert len(oauth_remote["tokens"]) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "test-client-secret", "token_type": "MAC", "expires_in": 120},
        {"access_token": "test-client-secret\n", "token_type": "Bearer", "expires_in": 120},
        {"access_token": "test-client-secret", "token_type": "Bearer", "expires_in": -1},
    ],
)
async def test_invalid_token_responses_are_redacted(fake_store, oauth_remote, payload):
    record = await oauth_record()
    oauth_remote["token_payload"] = payload
    async with client_for(record) as client:
        with pytest.raises(ValueError) as error:
            await client.post(record.url, json={})
    assert "test-client-secret" not in str(error.value)
    assert oauth_remote["calls"] == []


async def test_private_token_endpoint_is_blocked_before_credentials_are_sent(
    fake_store, oauth_remote, monkeypatch
):
    record = await oauth_record()
    monkeypatch.setattr(
        mcp_transport, "resolve_and_validate", lambda url: (False, "private", "auth.example", None)
    )
    async with client_for(record) as client:
        with pytest.raises(ValueError, match="OAuth"):
            await client.post(record.url, json={})
    assert oauth_remote["tokens"] == []
    assert oauth_remote["calls"] == []


async def test_token_redirect_is_not_followed(fake_store, oauth_remote):
    record = await oauth_record()
    oauth_remote["token_status"] = 302
    async with client_for(record) as client:
        with pytest.raises(ValueError, match="OAuth"):
            await client.post(record.url, json={})
    assert len(oauth_remote["tokens"]) == 1
    assert oauth_remote["calls"] == []


async def test_oauth_works_through_real_mcp_discovery_and_execution(fake_store, oauth_remote):
    def reply(request):
        payload = json.loads(request.content) if request.content else {}
        if "id" not in payload:
            return httpx.Response(202)
        method = payload["method"]
        result = {
            "initialize": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            },
            "tools/list": {
                "tools": [
                    {"name": "search", "description": "Search", "inputSchema": {"type": "object"}}
                ]
            },
            "tools/call": {"content": [{"type": "text", "text": "found"}], "isError": False},
        }[method]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    oauth_remote["reply"] = reply
    record = await oauth_record()
    definitions = await runtime.discover_tools(record, ("workspace_mcps",))
    assert [tool.name for tool in definitions] == ["search"]
    fake_store.seed(
        ["workspace_mcps"], record.name, {**record.model_dump(), "allowed_tools": ["search"]}
    )
    tools = await loader.load_workspace_mcp_tools()
    result = await tools[0].ainvoke({})
    assert result[0]["text"] == "found"
    assert len(oauth_remote["tokens"]) == 1
