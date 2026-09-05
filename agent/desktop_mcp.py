"""Run-scoped desktop MCP sessions backed by the authenticated Electron broker."""

import asyncio
import hashlib
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.auth import OAuthClientProvider
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)

_ENV_LITERAL = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}")
_oauth_locks: dict[str, asyncio.Lock] = {}
_BROKER_URL = os.environ.pop("OPEN_SWE_MCP_BROKER_URL", "")
_BROKER_TOKEN = os.environ.pop("OPEN_SWE_MCP_BROKER_TOKEN", "")


def _expand(value: str, env: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        if match[1] not in env:
            raise ValueError(f"MCP environment variable is not set: {match[1]}")
        return env[match[1]]

    return _ENV_LITERAL.sub(replace, value)


@asynccontextmanager
async def _broker() -> AsyncIterator[httpx.AsyncClient]:
    url = _BROKER_URL
    token = _BROKER_TOKEN
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not token
    ):
        raise RuntimeError("Trusted desktop MCP broker is not configured")
    async with httpx.AsyncClient(
        base_url=url,
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,
        follow_redirects=False,
        timeout=20,
    ) as client:
        yield client


async def _post(broker: httpx.AsyncClient, path: str, data: dict[str, Any]) -> Any:
    response = await broker.post(path, json=data)
    response.raise_for_status()
    return response.json()


async def local_connections() -> list[dict[str, Any]]:
    """Reread enabled connections for trusted loaders; never expose these credentials."""
    async with _broker() as broker:
        response = await broker.get("/runtime")
        response.raise_for_status()
        runtime = response.json()
    return await _connections(runtime)


async def _connections(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    connections = []
    env = runtime["env"]
    for server in runtime["servers"]:
        if not server["enabled"]:
            continue
        connection = {
            **server,
            "name": f"local_{re.sub(r'[^a-zA-Z0-9_-]', '_', server['name'])[:20]}_{hashlib.sha256(server['name'].encode()).hexdigest()[:8]}",
        }
        if server.get("url"):
            connection["url"] = _expand(server["url"], env)
            connection["headers"] = {
                key: _expand(value, env) for key, value in server.get("headers", {}).items()
            }
        else:
            child_env = {
                key: value
                for key, value in env.items()
                if not key.startswith(
                    ("OPEN_SWE_MCP_", "OPEN_SWE_LOCAL_", "OPEN_SWE_OPENAI_OAUTH_")
                )
            }
            for key in server.get("env_vars", []) + server.get("env_passthrough", []):
                if key not in child_env:
                    raise ValueError(f"MCP environment variable is not available: {key}")
            child_env.update(
                {key: _expand(value, env) for key, value in server.get("env", {}).items()}
            )
            connection.update(
                command=_expand(server["command"], env),
                args=[_expand(value, env) for value in server.get("args", [])],
                env=child_env,
                cwd=_expand(server["cwd"], env) if server.get("cwd") else None,
            )
        connection["local_name"] = server["name"]
        connections.append(connection)
    cloud = runtime.get("cloud")
    if cloud:
        base = cloud["backend_url"].rstrip("/")
        headers = {"Cookie": f"{cloud['cookie_name']}={cloud['session_token']}"}
        async with httpx.AsyncClient(
            headers=headers, follow_redirects=False, trust_env=False, timeout=20
        ) as client:
            response = await client.get(f"{base}/dashboard/api/mcp-connections")
            response.raise_for_status()
            for record in response.json()["connections"]:
                if record["enabled"] and record["name"] not in {
                    server["name"] for server in runtime["servers"]
                }:
                    connections.append(
                        {
                            "name": f"cloud_{record['id']}",
                            "transport": "streamable_http",
                            "url": f"{base}/dashboard/api/mcp-connections/{quote(record['id'], safe='')}/proxy",
                            "headers": headers.copy(),
                            "cloud": True,
                        }
                    )
    return connections


class _KeychainStorage:
    def __init__(self, broker: httpx.AsyncClient, name: str, key: str, record: dict[str, Any]):
        self.broker = broker
        self.name = name
        self.key = key
        self.record = record
        self.provider: OAuthClientProvider | None = None

    async def save(self) -> None:
        await _post(
            self.broker, "/credentials", {"name": self.name, "key": self.key, "value": self.record}
        )

    async def get_tokens(self) -> OAuthToken | None:
        tokens = self.record.get("tokens")
        return OAuthToken.model_validate(tokens) if tokens else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        value = tokens.model_dump(mode="json", exclude_none=True)
        previous = self.record.get("tokens", {})
        if not value.get("refresh_token") and previous.get("refresh_token"):
            value["refresh_token"] = previous["refresh_token"]
            tokens.refresh_token = previous["refresh_token"]
        self.record["tokens"] = value
        self.record["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in else None
        if self.provider:
            context = self.provider.context
            self.record["metadata"] = (
                context.oauth_metadata.model_dump(mode="json") if context.oauth_metadata else None
            )
            self.record["resource"] = (
                context.protected_resource_metadata.model_dump(mode="json")
                if context.protected_resource_metadata
                else None
            )
            self.record["auth_server_url"] = context.auth_server_url
        await self.save()

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        value = self.record.get("client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.record["client"] = client_info.model_dump(mode="json", exclude_none=True)
        await self.save()


class _LocalOAuthProvider(OAuthClientProvider):
    async def _initialize(self) -> None:
        await super()._initialize()
        record = self.context.storage.record
        self.context.token_expiry_time = record.get("expires_at")
        if record.get("metadata"):
            self.context.oauth_metadata = OAuthMetadata.model_validate(record["metadata"])
        if record.get("resource"):
            self.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(
                record["resource"]
            )
        self.context.auth_server_url = record.get("auth_server_url")


@asynccontextmanager
async def _oauth(
    broker: httpx.AsyncClient, connection: dict[str, Any]
) -> AsyncIterator[OAuthClientProvider]:
    name = connection["local_name"]
    async with _oauth_locks.setdefault(name, asyncio.Lock()):
        record = await _post(
            broker, "/credentials", {"name": name, "key": connection["credential_key"]}
        )
        callback: asyncio.Future[tuple[str, str | None]] = (
            asyncio.get_running_loop().create_future()
        )
        expected_state: str | None = None
        writers: set[asyncio.StreamWriter] = set()

        async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writers.add(writer)
            try:
                line = await asyncio.wait_for(reader.readline(), 10)
                method, target, _ = line.decode("ascii").strip().split(" ", 2)
                parsed = urlsplit(target)
                fields = parse_qs(parsed.query)
                state = fields.get("state", [""])[0]
                valid = (
                    method == "GET"
                    and parsed.path == "/callback"
                    and expected_state
                    and secrets.compare_digest(state, expected_state)
                )
                if valid and not callback.done():
                    if fields.get("error"):
                        callback.set_exception(RuntimeError("MCP OAuth authorization was denied"))
                    elif fields.get("code"):
                        callback.set_result((fields["code"][0], state))
                    else:
                        valid = False
                body = b"Return to Open SWE." if valid else b"Invalid OAuth callback."
                status = b"200 OK" if valid else b"400 Bad Request"
                writer.write(
                    b"HTTP/1.1 "
                    + status
                    + b"\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
            except ValueError, UnicodeError, TimeoutError, ConnectionError:
                pass
            finally:
                writer.close()
                writers.discard(writer)

        redirects = record.get("client", {}).get("redirect_uris", [])
        port = 0
        if redirects:
            parsed = urlsplit(redirects[0])
            if parsed.hostname == "127.0.0.1" and parsed.scheme == "http":
                port = parsed.port or 0
        listener = await asyncio.start_server(receive, "127.0.0.1", port, limit=16384)
        try:
            redirect_uri = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}/callback"
            storage = _KeychainStorage(broker, name, connection["credential_key"], record)
            metadata = OAuthClientMetadata(
                client_name="Open SWE Desktop",
                redirect_uris=[redirect_uri],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
                scope=connection.get("oauth_scope"),
            )
            if connection.get("oauth_client_id") and not record.get("client"):
                await storage.set_client_info(
                    OAuthClientInformationFull(
                        **metadata.model_dump(), client_id=connection["oauth_client_id"]
                    )
                )

            async def redirect(url: str) -> None:
                nonlocal expected_state, callback
                expected_state = parse_qs(urlsplit(url).query)["state"][0]
                callback = asyncio.get_running_loop().create_future()
                await _post(broker, "/open", {"url": url})

            async def wait_callback() -> tuple[str, str | None]:
                return await asyncio.wait_for(callback, 300)

            provider = _LocalOAuthProvider(
                server_url=connection["url"],
                client_metadata=metadata,
                storage=storage,
                redirect_handler=redirect,
                callback_handler=wait_callback,
            )
            storage.provider = provider
            yield provider
        finally:
            listener.close()
            await listener.wait_closed()
            for writer in writers.copy():
                writer.close()
            if not callback.done():
                callback.cancel()
            elif not callback.cancelled():
                callback.exception()


@asynccontextmanager
async def local_mcp_tools() -> AsyncIterator[list[BaseTool]]:
    """Keep local stdio/HTTP and authenticated cloud-proxy sessions alive for one run."""
    if not _BROKER_URL:
        yield []
        return
    async with AsyncExitStack() as stack:
        broker = await stack.enter_async_context(_broker())
        response = await broker.get("/runtime")
        response.raise_for_status()
        connections = await _connections(response.json())
        tools = []
        for connection in sorted(connections, key=lambda item: item["name"]):
            if connection.get("command"):
                transport = stdio_client(
                    StdioServerParameters(
                        command=connection["command"],
                        args=connection["args"],
                        env=connection["env"],
                        cwd=connection["cwd"],
                    )
                )
            else:
                auth = None
                if (
                    not connection.get("cloud")
                    and connection.get("auth_type") != "none"
                    and not any(key.lower() == "authorization" for key in connection["headers"])
                ):
                    auth = await stack.enter_async_context(_oauth(broker, connection))
                client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers={
                            **connection["headers"],
                            **({"Origin": "open-swe://app"} if connection.get("cloud") else {}),
                        },
                        auth=auth,
                        follow_redirects=False,
                        trust_env=False,
                        timeout=httpx.Timeout(30, read=300),
                    )
                )
                transport = streamable_http_client(connection["url"], http_client=client)
            streams = await stack.enter_async_context(transport)
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
            tools.extend(
                await load_mcp_tools(session, server_name=connection["name"], tool_name_prefix=True)
            )
        yield tools
