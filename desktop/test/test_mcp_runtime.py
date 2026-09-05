"""Focused desktop MCP lifecycle and OAuth protocol checks."""

import asyncio
import base64
import json
import os
import socket
import sys
import time
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx

from agent import desktop_mcp as mcp


class DesktopMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_sessions_are_held_and_closed(self):
        runtime = {
            "env": {"PATH": os.environ["PATH"], "VALUE": "literal-value"},
            "servers": [
                {
                    "name": "counter",
                    "enabled": True,
                    "command": sys.executable,
                    "args": [
                        "-c",
                        "from mcp.server.fastmcp import FastMCP\nm=FastMCP('counter')\nn=0\n@m.tool()\ndef count() -> str:\n global n\n n+=1\n return str(n)\nm.run()",
                    ],
                    "env": {"LITERAL": "${env:VALUE}:${VALUE}"},
                }
            ],
            "cloud": None,
        }
        connections = await mcp._connections(runtime)
        self.assertEqual(connections[0]["env"]["LITERAL"], "literal-value:literal-value")
        self.assertEqual(connections[0]["args"], runtime["servers"][0]["args"])
        runtime["servers"].extend(
            [
                {"name": "a-broken", "enabled": True, "command": "/missing/mcp-server"},
                {
                    "name": "z-broken",
                    "enabled": True,
                    "command": sys.executable,
                    "args": ["-c", "raise SystemExit(1)"],
                },
            ]
        )

        @asynccontextmanager
        async def broker():
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1",
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=runtime)),
            ) as client:
                yield client

        with patch.object(mcp, "_BROKER_URL", "configured"), patch.object(mcp, "_broker", broker):
            async with mcp.local_mcp_tools() as tools:
                self.assertEqual(len(tools), 1)
                first = await tools[0].ainvoke({})
                second = await tools[0].ainvoke({})
                self.assertIn("1", str(first))
                self.assertIn("2", str(second))
            with self.assertRaises(anyio.ClosedResourceError):
                await tools[0].ainvoke({})

    async def test_cloud_uses_only_backend_proxy_and_local_name_wins(self):
        requests = []
        real_client = httpx.AsyncClient

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "connections": [
                        {
                            "id": "a" * 32,
                            "name": "cloud",
                            "enabled": True,
                            "url": "https://upstream.invalid",
                            "headers": {"Secret": "must-not-copy"},
                        },
                        {"id": "b" * 32, "name": "override", "enabled": True},
                        {"id": "c" * 32, "name": "disabled", "enabled": False},
                    ]
                },
            )

        with patch.object(
            mcp.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)),
        ):
            connections = await mcp._connections(
                {
                    "env": {},
                    "servers": [{"name": "override", "enabled": False, "command": "ignored"}],
                    "cloud": {
                        "backend_url": "https://backend.example",
                        "cookie_name": "osw_session",
                        "session_token": "session",
                    },
                }
            )
        self.assertEqual(len(connections), 1)
        self.assertEqual(
            connections[0]["url"],
            f"https://backend.example/dashboard/api/mcp-connections/{'a' * 32}/proxy",
        )
        self.assertEqual(connections[0]["headers"], {"Cookie": "osw_session=session"})
        self.assertEqual(requests[0].headers["cookie"], "osw_session=session")
        self.assertNotIn("upstream.invalid", json.dumps(connections))
        self.assertNotIn("must-not-copy", json.dumps(connections))

    async def test_sdk_oauth_loopback_pkce_keychain_and_refresh(self, method=None):
        record = {"client_secret": "manual-secret"} if method and method != "none" else {}
        requests = []
        redirects = []
        refreshed = False
        server = "http://127.0.0.1:9876"

        async def post(broker, path, data):
            nonlocal record
            if path == "/credentials":
                if "value" in data:
                    record = json.loads(json.dumps(data["value"]))
                    return None
                return json.loads(json.dumps(record))
            self.assertEqual(path, "/open")
            query = parse_qs(urlsplit(data["url"]).query)
            redirects.append(query)
            self.assertEqual(query["code_challenge_method"], ["S256"])
            async with httpx.AsyncClient(trust_env=False) as browser:
                invalid = await browser.get(
                    query["redirect_uri"][0], params={"code": "bad", "state": "wrong"}
                )
                self.assertEqual(invalid.status_code, 400)
                valid = await browser.get(
                    query["redirect_uri"][0], params={"code": "code", "state": query["state"][0]}
                )
                self.assertEqual(valid.status_code, 200)
            return True

        def upstream(request):
            nonlocal refreshed
            requests.append(request)
            if request.url.path == "/mcp":
                if request.headers.get("authorization") in {"Bearer access", "Bearer refreshed"}:
                    return httpx.Response(200, json={"ok": True})
                return httpx.Response(
                    401,
                    headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{server}/.well-known/oauth-protected-resource"'
                    },
                )
            if request.url.path == "/.well-known/oauth-protected-resource":
                return httpx.Response(
                    200, json={"resource": server + "/mcp", "authorization_servers": [server]}
                )
            if request.url.path == "/.well-known/oauth-authorization-server":
                return httpx.Response(
                    200,
                    json={
                        "issuer": server,
                        "authorization_endpoint": server + "/authorize",
                        "token_endpoint": server + "/token",
                        "registration_endpoint": server + "/register",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                    },
                )
            if request.url.path == "/register":
                self.assertIsNone(method)
                return httpx.Response(
                    201, json={**json.loads(request.content), "client_id": "registered"}
                )
            if request.url.path == "/token":
                fields = parse_qs(request.content.decode())
                if method == "client_secret_basic":
                    self.assertEqual(
                        request.headers["authorization"],
                        "Basic " + base64.b64encode(b"manual-client:manual-secret").decode(),
                    )
                    self.assertNotIn("client_secret", fields)
                elif method == "client_secret_post":
                    self.assertEqual(fields["client_secret"], ["manual-secret"])
                else:
                    self.assertNotIn("client_secret", fields)
                if fields["grant_type"] == ["refresh_token"]:
                    self.assertEqual(fields["refresh_token"], ["refresh"])
                    refreshed = True
                    return httpx.Response(
                        200,
                        json={
                            "access_token": "refreshed",
                            "token_type": "Bearer",
                            "expires_in": 3600,
                        },
                    )
                self.assertEqual(fields["code"], ["code"])
                self.assertTrue(fields["code_verifier"])
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access",
                        "refresh_token": "refresh",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            return httpx.Response(404)

        connection = {"local_name": "oauth", "url": server + "/mcp", "credential_key": "key"}
        if method:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                redirect_uri = f"http://127.0.0.1:{listener.getsockname()[1]}/callback"
            connection.update(
                oauth_client_id="manual-client",
                oauth_redirect_uri=redirect_uri,
                oauth_token_endpoint_auth_method=method,
            )
        with patch.object(mcp, "_post", post):
            async with mcp._oauth(None, connection) as provider:
                async with httpx.AsyncClient(
                    auth=provider, transport=httpx.MockTransport(upstream)
                ) as client:
                    self.assertEqual((await client.get(server + "/mcp")).status_code, 200)
            self.assertEqual(record["tokens"]["refresh_token"], "refresh")
            self.assertEqual(
                record["client"]["client_id"], "manual-client" if method else "registered"
            )
            if method:
                self.assertEqual(redirects[0]["redirect_uri"], [redirect_uri])
            self.assertIsNotNone(record["metadata"])
            record["expires_at"] = time.time() - 10
            async with mcp._oauth(None, connection) as provider:
                async with httpx.AsyncClient(
                    auth=provider, transport=httpx.MockTransport(upstream)
                ) as client:
                    self.assertEqual((await client.get(server + "/mcp")).status_code, 200)
            self.assertTrue(refreshed)
            self.assertEqual(len(redirects), 1)
            self.assertEqual(record["tokens"]["refresh_token"], "refresh")
            port = urlsplit(redirects[0]["redirect_uri"][0]).port
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)

    async def test_manual_clients_use_registered_callback_and_auth_method(self):
        for method in ["none", "client_secret_basic", "client_secret_post"]:
            with self.subTest(method=method):
                await self.test_sdk_oauth_loopback_pkce_keychain_and_refresh(method)


if __name__ == "__main__":
    unittest.main()
