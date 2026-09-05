import asyncio
import base64
import hashlib
import json
import socket
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from langgraph_sdk.errors import ConflictError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.requests import Request

from agent import store
from agent.dashboard import mcp_connections as mc
from agent.dashboard import mcp_http as mh
from agent.dashboard import mcp_oauth as mo
from agent.utils.distributed_lock import distributed_lock


@pytest.fixture
async def environment(monkeypatch):
    items = {}

    class Store:
        async def get_item(self, namespace, key):
            return {"value": items.get((tuple(namespace), key))}

        async def put_item(self, namespace, key, value):
            items[(tuple(namespace), key)] = value

        async def delete_item(self, namespace, key):
            items.pop((tuple(namespace), key), None)

        async def search_items(self, namespace, **kwargs):
            values = [
                {"value": value} for (ns, _), value in items.items() if ns == tuple(namespace)
            ]
            return {"items": values[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]]}

    claims = set()

    class Threads:
        async def create(self, *, thread_id, if_exists, ttl):
            await asyncio.sleep(0)
            assert if_exists == "raise"
            assert ttl == {"strategy": "keep_latest", "ttl": 60}
            if thread_id in claims:
                raise ConflictError(
                    "Already owned",
                    response=httpx.Response(
                        409, request=httpx.Request("POST", "http://store/threads")
                    ),
                    body=None,
                )
            claims.add(thread_id)

        async def delete(self, thread_id):
            await asyncio.sleep(0)
            claims.remove(thread_id)

    monkeypatch.setattr(
        store, "store_client", lambda: SimpleNamespace(store=Store(), threads=Threads())
    )
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    async def dns(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)

    @asynccontextmanager
    async def stream(*args, **kwargs):
        yield (None, None, None)

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self, params=None):
            cursor = params.cursor if params else None
            return SimpleNamespace(
                tools=[SimpleNamespace(name="search" if cursor is None else "fetch")],
                nextCursor="next" if cursor is None else None,
            )

    monkeypatch.setattr(mc, "streamable_http_client", stream)
    monkeypatch.setattr(mc, "ClientSession", Session)
    return items


async def create(auth_type="bearer", **kwargs):
    return await mc.save_connection(
        "alice",
        {
            "name": "Example",
            "url": "https://example.com/mcp",
            "auth_type": auth_type,
            "bearer_token": "secret-token",
            **kwargs,
        },
    )


async def test_cancelled_owner_is_not_replaced(environment):
    entered = asyncio.Event()

    async def owner():
        async with distributed_lock(["cancelled-oauth"]):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(TimeoutError):
        async with distributed_lock(["cancelled-oauth"], timeout=0):
            pytest.fail("An abandoned owner must not be replaced without fencing")


async def test_real_sdk_initialization_and_paginated_discovery(environment, monkeypatch):
    requests = []

    async def upstream(request):
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(await request.aread())
        requests.append(message)
        if "id" not in message:
            return httpx.Response(202)
        if message["method"] == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
        else:
            cursor = message.get("params", {}).get("cursor")
            result = {
                "tools": [
                    {"name": "fetch" if cursor else "search", "inputSchema": {"type": "object"}}
                ]
            }
            if not cursor:
                result["nextCursor"] = "page2"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    monkeypatch.setattr(mc, "ClientSession", ClientSession)
    monkeypatch.setattr(mc, "streamable_http_client", streamable_http_client)
    monkeypatch.setattr(
        mc,
        "safe_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    result = await create()
    assert result["status"] == "connected"
    assert result["tool_names"] == ["fetch", "search"]
    assert requests[0]["method"] == "initialize"
    assert sum(message["method"] == "tools/list" for message in requests) == 2


async def test_encryption_crud_catalog_owner_and_url_change(environment):
    record = await create()
    assert record["status"] == "connected"
    assert record["tool_names"] == ["fetch", "search"]
    assert record["bearer_token_configured"]
    assert "secret-token" not in json.dumps(record)
    assert "secret-token" not in json.dumps(list(environment.values()))
    assert await mc.list_connections("bob") == []
    for operation in (mc.connection_config, mc.discover_connection, mc.delete_connection):
        with pytest.raises(mh.MCPConnectionError, match="not found"):
            await operation("bob", record["id"])
    with pytest.raises(mh.MCPConnectionError, match="not found"):
        await mc.save_connection("bob", {"id": record["id"], "name": "stolen"})
    preserved = await mc.save_connection("alice", {"id": record["id"], "name": "Renamed"})
    assert preserved["bearer_token_configured"]
    disabled = await mc.save_connection("alice", {"id": record["id"], "enabled": False})
    assert not disabled["enabled"]
    with pytest.raises(mh.MCPConnectionError, match="disabled"):
        await mc.connection_config("alice", record["id"])
    await mc.save_connection(
        "alice", {"id": record["id"], "oauth_client_id": "manual", "oauth_client_secret": "secret"}
    )
    changed = await mc.save_connection(
        "alice", {"id": record["id"], "url": "https://other.example/mcp"}
    )
    assert not changed["bearer_token_configured"]
    assert not changed["oauth_client_configured"]
    assert not changed["oauth_client_secret_configured"]
    await mc.delete_connection("alice", record["id"])
    assert await mc.list_connections("alice") == []


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "2002:7f00:1::",
        "64:ff9b:1::7f00:1",
    ],
)
async def test_ssrf_rejects_private_and_mixed_answers(monkeypatch, ip):
    async def dns(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
            for address in ("93.184.216.34", ip)
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    with pytest.raises(mh.MCPConnectionError, match="public"):
        await mh.resolve_url("https://attacker.example/mcp")


async def test_transport_pins_dns_sni_host_and_rejects_redirects(environment, monkeypatch):
    seen = []

    async def upstream(request):
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://127.0.0.1/secret"})

    transport = mh.PinnedTransport()
    transport.transport = httpx.MockTransport(upstream)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(mh.MCPConnectionError, match="redirects"):
            await client.post(
                "https://example.com/mcp",
                headers={"Host": "attacker", "Authorization": "Bearer secret"},
            )
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "https://user:secret@example.com/mcp",
        "https://example.com/mcp?token=secret",
        "https://example.com/mcp#secret",
        "file:///etc/passwd",
    ],
)
async def test_unsafe_url_forms(url):
    with pytest.raises(mh.MCPConnectionError):
        await mh.resolve_url(url)


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil"},
        {"x-key": "secret\r\ninjected: value"},
        {"Authorization": "secret"},
        {"Cookie": "session"},
        {"Mcp-Session-Id": "other-user"},
    ],
)
async def test_header_injection_rejected(environment, headers):
    with pytest.raises(mh.MCPConnectionError):
        await create("headers", headers=headers)
    assert not environment


def proxy_request(method="POST", headers=None, body=b'{"jsonrpc":"2.0","method":"ping","id":1}'):
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "https",
            "server": ("dashboard.example", 443),
            "path": "/proxy",
            "query_string": b"",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
            ],
        },
        receive,
    )


async def test_proxy_streaming_session_binding_and_dashboard_credentials(environment, monkeypatch):
    public = await create()
    seen = []
    closed = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"event: message\ndata: "
            yield b'{"result":{}}\n\n'

        async def aclose(self):
            closed.append(True)

    async def upstream(request):
        seen.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "mcp-session-id": "upstream-secret",
                "set-cookie": "do-not-forward",
            },
            stream=Stream(),
        )

    monkeypatch.setattr(
        mc,
        "safe_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    response = await mc.proxy_connection(
        proxy_request(headers={"Authorization": "dashboard-secret", "Cookie": "dashboard-cookie"}),
        "alice",
        public["id"],
    )
    assert not closed
    token = response.headers["mcp-session-id"]
    assert token != "upstream-secret"
    assert "set-cookie" not in response.headers
    assert (
        b"".join([part async for part in response.body_iterator])
        == b'event: message\ndata: {"result":{}}\n\n'
    )
    assert closed
    assert seen[0].headers["authorization"] == "Bearer secret-token"
    assert "cookie" not in seen[0].headers
    record = await mc._get("alice", public["id"])
    assert mc._upstream_session("alice", record, token) == "upstream-secret"
    with pytest.raises(mh.MCPConnectionError):
        mc._upstream_session("bob", record, token)
    with pytest.raises(mh.MCPConnectionError):
        await mc.proxy_connection(proxy_request(), "bob", public["id"])
    assert len(seen) == 1


async def test_oauth_pkce_callback_replay_encryption_and_refresh(environment, monkeypatch):
    public = await create("oauth")
    calls = []
    metadata = {
        "issuer": "https://auth.example/",
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "https://auth.example/register",
        "token_endpoint_auth_methods_supported": ["none"],
    }

    async def discover(url, authorization_server=""):
        assert not authorization_server
        return metadata, url, "read"

    async def request_json(method, url, **kwargs):
        calls.append((url, kwargs))
        await asyncio.sleep(0.02)
        if url.endswith("register"):
            return {**kwargs["json"], "client_id": "client"}
        return {
            "access_token": "oauth-access-secret",
            "refresh_token": "oauth-refresh-secret",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    monkeypatch.setattr(mo, "_discover", discover)
    monkeypatch.setattr(mo, "request_json", request_json)
    url = await mo.start_oauth("alice", public["id"], "https://dashboard.example/callback")
    query = parse_qs(urlsplit(url).query)
    state = query["state"][0]
    flows = [
        mc._unseal(value)
        for (namespace, _), value in environment.items()
        if namespace[0] == mo._FLOW_NAMESPACE
    ]
    verifier = flows[0]["verifier"]
    assert query["code_challenge"][0] == base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    assert query["code_challenge_method"] == ["S256"]
    results = await asyncio.gather(
        mo.finish_oauth(state, "authorization-code"),
        mo.finish_oauth(state, "authorization-code"),
        return_exceptions=True,
    )
    result = next(result for result in results if isinstance(result, dict))
    replay = next(result for result in results if isinstance(result, mh.MCPConnectionError))
    assert replay.status_code == 400
    assert sum(url.endswith("token") for url, _ in calls) == 1
    assert result["oauth_configured"]
    assert "oauth-access-secret" not in json.dumps(result)
    assert "oauth-refresh-secret" not in json.dumps(list(environment.values()))
    assert calls[-1][1]["data"]["code_verifier"] == verifier
    assert calls[-1][1]["data"]["resource"] == "https://example.com/mcp"
    with pytest.raises(mh.MCPConnectionError, match="state"):
        await mo.finish_oauth(state, "authorization-code")
    record = await mc._get("alice", public["id"])
    record["oauth"]["expires_at"] = time.time() - 1
    await mc._put("alice", record)
    stale = await mc._get("alice", public["id"])
    refreshed = await asyncio.gather(
        mo.access_token("alice", record), mo.access_token("alice", stale)
    )
    assert refreshed == ["oauth-access-secret", "oauth-access-secret"]
    assert (
        sum(kwargs.get("data", {}).get("grant_type") == "refresh_token" for _, kwargs in calls) == 1
    )
    config = await mc.connection_config("alice", public["id"])
    assert config["headers"] == {}
    flow = config["auth"].async_auth_flow(httpx.Request("POST", config["url"]))
    request = await anext(flow)
    assert request.headers["authorization"] == "Bearer oauth-access-secret"
    await flow.aclose()
    assert calls[-1][1]["data"]["grant_type"] == "refresh_token"
    record = await mc._get("alice", public["id"])
    record["oauth"]["expires_at"] = time.time() - 1
    await mc._put("alice", record)
    attempts = 0

    async def interrupted(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.02)
        raise mh.MCPConnectionError(502, "OAuth endpoint rejected the request")

    monkeypatch.setattr(mo, "request_json", interrupted)
    failures = await asyncio.gather(
        mo.access_token("alice", record), mo.access_token("alice", record), return_exceptions=True
    )
    assert attempts == 1
    assert sorted(error.status_code for error in failures) == [409, 502]
    assert (await mc._get("alice", public["id"]))["oauth"]["refresh_pending"]
    await mc.delete_connection("alice", public["id"])
    with pytest.raises(mh.MCPConnectionError, match="not found"):
        await anext(config["auth"].async_auth_flow(httpx.Request("POST", config["url"])))


async def test_oauth_discovery_rejects_ssrf_from_challenge(environment, monkeypatch):
    async def upstream(request):
        return httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer resource_metadata="https://127.0.0.1/metadata"'},
        )

    async def dns(host, port, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", port),
            )
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(
        mo,
        "safe_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    with pytest.raises(mh.MCPConnectionError, match="public"):
        await mo._discover("https://example.com/mcp")


@pytest.mark.parametrize(
    "bad_field", ["issuer", "token_endpoint", "registration_endpoint", "authorization_endpoint"]
)
async def test_oauth_metadata_endpoint_validation(environment, monkeypatch, bad_field):
    async def upstream(request):
        return httpx.Response(401)

    async def metadata(urls):
        if "oauth-protected-resource" in urls[0]:
            return {
                "resource": "https://example.com/mcp",
                "authorization_servers": ["https://auth.example/"],
            }
        return {
            "issuer": "https://auth.example/",
            "authorization_endpoint": "https://auth.example/authorize",
            "token_endpoint": "https://auth.example/token",
            "registration_endpoint": "https://auth.example/register",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            bad_field: "https://127.0.0.1/private",
        }

    async def dns(host, port, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", port),
            )
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(
        mo,
        "safe_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    monkeypatch.setattr(mo, "_metadata", metadata)
    with pytest.raises(mh.MCPConnectionError):
        await mo._discover("https://example.com/mcp")


async def test_manual_client_fallback_and_stale_callback(environment, monkeypatch):
    metadata = {"token_endpoint_auth_methods_supported": ["client_secret_post"]}
    with pytest.raises(mh.MCPConnectionError, match="manually registered"):
        await mo._client(
            {"oauth_token_endpoint_auth_method": "client_secret_post"},
            metadata,
            "https://dashboard.example/callback",
            "read",
        )
    client = await mo._client(
        {
            "oauth_client_id": "manual",
            "oauth_client_secret": "secret",
            "oauth_token_endpoint_auth_method": "client_secret_post",
        },
        metadata,
        "https://dashboard.example/callback",
        "read",
    )
    assert client["client_id"] == "manual"
    metadata.update(
        issuer="https://auth.example/tenant",
        authorization_endpoint="https://auth.example/authorize",
        token_endpoint="https://auth.example/token",
        response_types_supported=["code"],
        code_challenge_methods_supported=["S256"],
    )
    prm = None
    prm_status = 404
    seen = []

    async def upstream(request):
        seen.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == request.extensions["sni_hostname"]
        if "oauth-protected-resource" in request.url.path:
            return httpx.Response(prm_status, json=prm)
        if request.url.path == "/.well-known/oauth-authorization-server/tenant":
            assert request.headers["host"] == "auth.example"
            return httpx.Response(200, json=metadata)
        if request.url.path == "/token":
            fields = parse_qs((await request.aread()).decode())
            assert fields["resource"] == ["https://example.com/mcp"]
            assert fields["client_secret"] == ["secret"]
            return httpx.Response(200, json={"access_token": "access", "token_type": "Bearer"})
        return httpx.Response(401)

    async def dns(host, port, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if host == "private.example" else "93.184.216.34", port),
            )
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(upstream))
    for issuer in ("http://auth.example", "https://private.example"):
        with pytest.raises(mh.MCPConnectionError):
            await create("oauth", oauth_authorization_server=issuer)
        with pytest.raises(mh.MCPConnectionError):
            await mo._discover("https://example.com/mcp", issuer)
    assert not seen
    record = await create(
        "oauth",
        oauth_client_id="manual",
        oauth_client_secret="secret",
        oauth_scope="read",
        oauth_token_endpoint_auth_method="client_secret_post",
        oauth_authorization_server="https://auth.example/tenant",
    )
    fields = {
        key: record[key]
        for key in (
            "oauth_client_id",
            "oauth_scope",
            "oauth_token_endpoint_auth_method",
            "oauth_authorization_server",
        )
    }
    assert fields == {
        "oauth_client_id": "manual",
        "oauth_scope": "read",
        "oauth_token_endpoint_auth_method": "client_secret_post",
        "oauth_authorization_server": "https://auth.example/tenant",
    }
    assert "oauth_client_secret" not in record
    edited = await mc.save_connection("alice", {"id": record["id"], "name": "Edited", **fields})
    assert all(edited[key] == value for key, value in fields.items())
    assert edited["oauth_client_secret_configured"]
    with pytest.raises(mh.MCPConnectionError, match="unavailable"):
        await mo._discover(record["url"])
    for status in (404, 405):
        prm_status = status
        url = await mo.start_oauth("alice", record["id"], "https://dashboard.example/callback")
        query = parse_qs(urlsplit(url).query)
        assert query["resource"] == [record["url"]]
        assert query["scope"] == ["read"]
        assert query["client_id"] == ["manual"]
    result = await mo.finish_oauth(query["state"][0], "code")
    assert result["oauth_configured"]
    for status, body, message in (
        (500, None, "discovery failed"),
        (200, {}, "Invalid MCP OAuth"),
        (
            200,
            {
                "resource": "https://other.example/mcp",
                "authorization_servers": [metadata["issuer"]],
            },
            "resource does not match",
        ),
        (
            200,
            {"resource": record["url"], "authorization_servers": ["https://other.example/"]},
            "conflicts",
        ),
    ):
        prm_status, prm = status, body
        with pytest.raises(mh.MCPConnectionError, match=message):
            await mo._discover(record["url"], fields["oauth_authorization_server"])
    prm_status, prm = 404, None
    for field, value, message in (
        ("issuer", "https://other.example/", "issuer does not match"),
        ("token_endpoint", "https://private.example/token", "public"),
        ("code_challenge_methods_supported", ["plain"], "S256"),
    ):
        original = metadata[field]
        metadata[field] = value
        with pytest.raises(mh.MCPConnectionError, match=message):
            await mo._discover(record["url"], fields["oauth_authorization_server"])
        metadata[field] = original
    url = await mo.start_oauth("alice", record["id"], "https://dashboard.example/callback")
    state = parse_qs(urlsplit(url).query)["state"][0]
    changed = await mc.save_connection(
        "alice", {"id": record["id"], "oauth_authorization_server": ""}
    )
    assert not changed["oauth_authorization_server"]
    assert not changed["oauth_configured"]
    with pytest.raises(mh.MCPConnectionError, match="changed"):
        await mo.finish_oauth(state, "code")


async def test_metadata_error_redaction(environment, monkeypatch):
    async def upstream(request):
        return httpx.Response(
            400, json={"error": "secret-token", "error_description": "refresh-secret"}
        )

    monkeypatch.setattr(
        mh,
        "safe_client",
        lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    with pytest.raises(mh.MCPConnectionError) as error:
        await mh.request_json("POST", "https://example.com/token")
    assert "secret" not in str(error.value)
