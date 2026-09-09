"""Owner-scoped MCP connections: storage, validation, discovery, OAuth and the desktop proxy."""

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
from starlette.requests import Request

from agent import store
from agent.dashboard import mcp_connections as mc
from agent.dashboard import mcp_http as mh
from agent.dashboard import mcp_oauth as mo
from agent.utils import url_safety


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

    monkeypatch.setattr(store, "store_client", lambda: SimpleNamespace(store=Store()))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

    def dns(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", dns)

    class Session:
        def __init__(self, connection):
            self.connection = connection

        async def initialize(self):
            pass

        async def list_tools(self, params=None):
            cursor = params.cursor if params else None
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="search" if cursor is None else "fetch", description="Find things"
                    )
                ],
                nextCursor="next" if cursor is None else None,
            )

    @asynccontextmanager
    async def create_session(connection, **kwargs):
        yield Session(connection)

    monkeypatch.setattr(mc, "create_session", create_session)
    return items


async def create(auth_type="bearer", owner="alice", **kwargs):
    return await mc.save_connection(
        owner,
        {
            "name": "Example" if owner != mc.WORKSPACE_OWNER else "example",
            "url": "https://example.com/mcp",
            "auth_type": auth_type,
            "bearer_token": "secret-token",
            **kwargs,
        },
    )


async def test_encryption_crud_catalog_owner_and_url_change(environment):
    record = await create()
    assert record["status"] == "connected"
    assert record["tool_names"] == ["fetch", "search"]
    assert record["tools"][0] == {"name": "search", "description": "Find things"}
    assert record["scope"] == "user"
    assert record["bearer_token_configured"]
    assert "secret-token" not in json.dumps(record)
    assert "secret-token" not in json.dumps(list(environment.values()))
    assert await mc.list_connections("bob") == []
    for operation in (mc.connection_config, mc.discover_connection):
        with pytest.raises(mh.MCPConnectionError, match="not found"):
            await operation("bob", record["id"])
    await mc.delete_connection("bob", record["id"])
    assert await mc.list_connections("alice") == [record]
    with pytest.raises(mh.MCPConnectionError, match="not found") as error:
        await mc.delete_connection("alice", "malformed")
    assert error.value.status_code == 404
    with pytest.raises(mh.MCPConnectionError, match="not found"):
        await mc.save_connection("bob", {"id": record["id"], "name": "stolen"})
    preserved = await mc.save_connection("alice", {"id": record["id"], "name": "Renamed"})
    assert preserved["bearer_token_configured"]
    assert preserved["revision"] != record["revision"]
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


async def test_concurrent_writers_are_detected_instead_of_overwriting(environment):
    record = await create()
    stored = await mc.get_record("alice", record["id"])
    stale = dict(stored)
    stored["name"] = "first"
    await mc.put_record("alice", stored, expected_version=stored["version"])
    stale["name"] = "second"
    with pytest.raises(mh.MCPConnectionError, match="changed") as error:
        await mc.put_record("alice", stale, expected_version=stale["version"])
    assert error.value.status_code == 409
    assert (await mc.get_record("alice", record["id"]))["name"] == "first"

    async def rename(current):
        current["name"] = "third"

    updated, _ = await mc.update_record("alice", record["id"], rename)
    assert updated["name"] == "third"
    with pytest.raises(mh.MCPConnectionError, match="changed"):
        await mc.put_record("alice", {**updated, "id": "f" * 32}, expected_version="missing")


async def test_workspace_scope_rules_and_allowlist(environment):
    with pytest.raises(mh.MCPConnectionError, match="lowercase"):
        await create("headers", owner=mc.WORKSPACE_OWNER, name="Incident", headers={"X-Key": "k"})
    with pytest.raises(mh.MCPConnectionError, match="OAuth"):
        await create("oauth", owner=mc.WORKSPACE_OWNER)
    shared = await create("headers", owner=mc.WORKSPACE_OWNER, headers={"X-Key": "k"})
    assert shared["scope"] == "workspace"
    assert shared["header_names"] == ["x-key"]
    assert shared["allowed_tools"] is None
    record = await mc.get_record(mc.WORKSPACE_OWNER, shared["id"])
    assert mc.runnable_tools(record) == [], "workspace tools run only when explicitly allowed"
    allowed = await mc.save_connection(
        mc.WORKSPACE_OWNER, {"id": shared["id"], "allowed_tools": ["search", "search", "gone"]}
    )
    assert allowed["allowed_tools"] == ["search", "gone"]
    assert mc.runnable_tools(await mc.get_record(mc.WORKSPACE_OWNER, shared["id"])) == ["search"]
    assert await mc.reveal_headers(mc.WORKSPACE_OWNER, shared["id"]) == {"x-key": "k"}
    with pytest.raises(mh.MCPConnectionError, match="not found"):
        await mc.reveal_headers("alice", shared["id"])
    assert await mc.list_connections("alice") == []
    personal = await create(allowed_tools=None)
    assert mc.runnable_tools(await mc.get_record("alice", personal["id"])) == ["fetch", "search"]
    with pytest.raises(mh.MCPConnectionError):
        await create(url="https://example.com/mcp?api_key=secret")
    sse = await create(
        "headers",
        owner=mc.WORKSPACE_OWNER,
        name="dd",
        transport="sse",
        url="https://mcp.example/v1/mcp?toolsets=core",
        headers={"DD_API_KEY": "k"},
    )
    assert sse["transport"] == "sse"
    assert sse["url"].endswith("?toolsets=core")


async def test_draft_discovery_persists_nothing(environment, monkeypatch):
    tools = await mc.discover_draft(
        "alice",
        {
            "name": "Draft",
            "url": "https://example.com/mcp",
            "auth_type": "bearer",
            "bearer_token": "t",
        },
    )
    assert [tool["name"] for tool in tools] == ["search", "fetch"]
    assert await mc.list_connections("alice") == []

    @asynccontextmanager
    async def failing(connection, **kwargs):
        raise httpx.HTTPStatusError(
            "denied",
            request=httpx.Request("POST", "https://example.com/mcp"),
            response=httpx.Response(401),
        )
        yield

    monkeypatch.setattr(mc, "create_session", failing)
    with pytest.raises(mh.MCPConnectionError, match="HTTP 401") as error:
        await mc.discover_draft("alice", {"name": "Draft", "url": "https://example.com/mcp"})
    assert error.value.status_code == 400


async def test_legacy_workspace_records_migrate_once(environment):
    environment[(("workspace_mcps",), "incident")] = {
        "name": "incident",
        "url": "https://mcp.incident.io/mcp",
        "transport": "streamable_http",
        "enabled": True,
        "allowed_tools": ["incident_list"],
        "encrypted_headers": mc.encrypt_token(json.dumps({"Authorization": "Bearer legacy"})),
        "header_names": ["Authorization"],
        "revision": "r1",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    listed = await mc.list_connections(mc.WORKSPACE_OWNER)
    assert [record["name"] for record in listed] == ["incident"]
    assert listed[0]["allowed_tools"] == ["incident_list"]
    assert listed[0]["headers_configured"]
    assert (("workspace_mcps",), "incident") not in environment
    assert await mc.list_connections(mc.WORKSPACE_OWNER) == listed


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
    def dns(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
            for address in ("93.184.216.34", ip)
        ]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", dns)
    with pytest.raises(mh.MCPConnectionError, match="public"):
        await mh.resolve_url("https://attacker.example/mcp")


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
        {"X-Key": "one", "x-key": "two"},
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
        "mcp_http_client",
        lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
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
    record = await mc.get_record("alice", public["id"])
    assert mc._upstream_session("alice", record, token) == "upstream-secret"
    with pytest.raises(mh.MCPConnectionError):
        mc._upstream_session("bob", record, token)
    with pytest.raises(mh.MCPConnectionError):
        await mc.proxy_connection(proxy_request(), "bob", public["id"])
    assert len(seen) == 1


def _metadata():
    return {
        "issuer": "https://auth.example/",
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "https://auth.example/register",
        "token_endpoint_auth_methods_supported": ["none"],
    }


async def test_oauth_pkce_callback_replay_encryption_and_refresh(environment, monkeypatch):
    public = await create("oauth")
    calls = []
    metadata = _metadata()

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
        mc.unseal(value)
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
    assert sum(url.endswith("token") for url, _ in calls) == 1, "a code is exchanged once"
    assert result["oauth_configured"]
    assert "oauth-access-secret" not in json.dumps(result)
    assert "oauth-refresh-secret" not in json.dumps(list(environment.values()))
    assert calls[-1][1]["data"]["code_verifier"] == verifier
    assert calls[-1][1]["data"]["resource"] == "https://example.com/mcp"
    with pytest.raises(mh.MCPConnectionError, match="state"):
        await mo.finish_oauth(state, "authorization-code")

    async def expire(current):
        current["oauth"]["expires_at"] = time.time() - 1

    record, _ = await mc.update_record("alice", public["id"], expire)
    stale = dict(record)
    refreshed = await asyncio.gather(
        mo.access_token("alice", record), mo.access_token("alice", stale)
    )
    assert refreshed == ["oauth-access-secret", "oauth-access-secret"]
    assert (
        sum(kwargs.get("data", {}).get("grant_type") == "refresh_token" for _, kwargs in calls) == 1
    ), "concurrent callers share one refresh"
    config = await mc.connection_config("alice", public["id"])
    assert config["headers"] == {}
    flow = config["auth"].async_auth_flow(httpx.Request("POST", config["url"]))
    request = await anext(flow)
    assert request.headers["authorization"] == "Bearer oauth-access-secret"
    await flow.aclose()
    record, _ = await mc.update_record("alice", public["id"], expire)
    attempts = 0

    async def interrupted(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.02)
        raise mh.MCPConnectionError(502, "OAuth endpoint rejected the request")

    monkeypatch.setattr(mo, "request_json", interrupted)
    failures = await asyncio.gather(
        mo.access_token("alice", record),
        mo.access_token("alice", dict(record)),
        return_exceptions=True,
    )
    assert attempts == 2, "each caller retries in turn after a handled failure"
    assert sorted(error.status_code for error in failures) == [502, 502]
    assert "refresh" not in (await mc.get_record("alice", public["id"]))["oauth"]
    await mc.delete_connection("alice", public["id"])
    with pytest.raises(mh.MCPConnectionError, match="not found"):
        await anext(config["auth"].async_auth_flow(httpx.Request("POST", config["url"])))


async def test_stale_refresh_claim_is_taken_over(environment, monkeypatch):
    public = await create("oauth")

    async def seed(current):
        current["oauth"] = {
            "metadata": _metadata(),
            "resource": current["url"],
            "client": {"client_id": "client", "token_endpoint_auth_method": "none"},
            "tokens": {"access_token": "old", "refresh_token": "refresh", "token_type": "Bearer"},
            "expires_at": time.time() - 1,
            "refresh": {"id": "dead-worker", "started_at": time.time() - 3600},
        }

    record, _ = await mc.update_record("alice", public["id"], seed)

    async def request_json(method, url, **kwargs):
        return {"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}

    monkeypatch.setattr(mo, "request_json", request_json)
    assert await mo.access_token("alice", record) == "fresh"
    stored = await mc.get_record("alice", public["id"])
    assert stored["oauth"]["tokens"]["refresh_token"] == "refresh"
    assert "refresh" not in stored["oauth"]

    async def live_claim(current):
        current["oauth"]["expires_at"] = time.time() - 1
        current["oauth"]["refresh"] = {"id": "other-worker", "started_at": time.time()}

    record, _ = await mc.update_record("alice", public["id"], live_claim)
    monkeypatch.setattr(mo, "_REFRESH_WAIT", 0.3)
    with pytest.raises(mh.MCPConnectionError, match="taking too long"):
        await mo.access_token("alice", record)


async def test_oauth_discovery_rejects_ssrf_from_challenge(environment, monkeypatch):
    async def upstream(request):
        return httpx.Response(
            401,
            headers={"WWW-Authenticate": 'Bearer resource_metadata="https://127.0.0.1/metadata"'},
        )

    def dns(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 443),
            )
        ]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", dns)
    monkeypatch.setattr(
        mo,
        "mcp_http_client",
        lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
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

    def dns(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 443),
            )
        ]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", dns)
    monkeypatch.setattr(
        mo,
        "mcp_http_client",
        lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
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

    def dns(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if host == "private.example" else "93.184.216.34", 443),
            )
        ]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", dns)
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
    changed = await mc.save_connection(
        "alice", {"id": record["id"], "oauth_client_id": "replacement"}
    )
    assert changed["oauth_client_id"] == "replacement"
    assert not changed["oauth_client_secret_configured"]
    assert not changed["oauth_configured"]
    assert (await mc.get_record("alice", record["id"]))["oauth_client_secret"] == ""
    changed = await mc.save_connection(
        "alice", {"id": record["id"], **fields, "oauth_client_secret": "secret"}
    )
    assert changed["oauth_client_secret_configured"]
    url = await mo.start_oauth("alice", record["id"], "https://dashboard.example/callback")
    state = parse_qs(urlsplit(url).query)["state"][0]
    changed = await mc.save_connection(
        "alice", {"id": record["id"], "oauth_authorization_server": ""}
    )
    assert not changed["oauth_authorization_server"]
    assert not changed["oauth_configured"]
    assert not changed["oauth_client_configured"]
    assert not changed["oauth_client_secret_configured"]
    with pytest.raises(mh.MCPConnectionError, match="changed"):
        await mo.finish_oauth(state, "code")
    changed = await mc.save_connection(
        "alice", {"id": record["id"], **fields, "oauth_client_secret": "replacement-secret"}
    )
    assert changed["oauth_client_configured"]
    assert (await mc.get_record("alice", record["id"]))[
        "oauth_client_secret"
    ] == "replacement-secret"


async def test_metadata_error_redaction(environment, monkeypatch):
    async def upstream(request):
        return httpx.Response(
            400, json={"error": "secret-token", "error_description": "refresh-secret"}
        )

    monkeypatch.setattr(
        mh,
        "mcp_http_client",
        lambda *args, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    with pytest.raises(mh.MCPConnectionError) as error:
        await mh.request_json("POST", "https://example.com/token")
    assert "secret" not in str(error.value)


async def test_desktop_handoff_flow_is_pinned_to_its_owner(environment, monkeypatch):
    public = await create("oauth")
    metadata = _metadata()

    async def discover(url, authorization_server=""):
        return metadata, url, "read"

    async def request_json(method, url, **kwargs):
        if url.endswith("register"):
            return {**kwargs["json"], "client_id": "client"}
        return {"access_token": "access", "token_type": "Bearer", "expires_in": 3600}

    monkeypatch.setattr(mo, "_discover", discover)
    monkeypatch.setattr(mo, "request_json", request_json)
    url = await mo.start_oauth(
        "alice", public["id"], "https://dashboard.example/callback", handoff=("challenge", 51234)
    )
    state = parse_qs(urlsplit(url).query)["state"][0]
    assert await mo.flow_handoff(state) == ("challenge", 51234)
    assert await mo.flow_handoff("not-a-state") is None
    with pytest.raises(mh.MCPConnectionError, match="state"):
        await mo.finish_oauth(state, "code", owner="bob")
    assert await mo.flow_handoff(state) is None, "a rejected exchange consumes the flow"
    browser = await mo.start_oauth("alice", public["id"], "https://dashboard.example/callback")
    browser_state = parse_qs(urlsplit(browser).query)["state"][0]
    assert await mo.flow_handoff(browser_state) is None
    assert (await mo.finish_oauth(browser_state, "code", owner="alice"))["oauth_configured"]
