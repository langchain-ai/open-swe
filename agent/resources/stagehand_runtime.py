import asyncio
import base64
import contextlib
import ipaddress
import json
import os
import socket
import sys
from typing import Any
from urllib.parse import urlparse

from stagehand import AsyncStagehand

_CLIENT: Any = None
_SESSION: Any = None
_PROXY: asyncio.Server | None = None
_PROXY_PORT: int | None = None
_ALLOWED_NON_GLOBAL_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"),)


def _resolve(url: str) -> tuple[bool, str, str | None]:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False, "unsupported or invalid URL", None
        addresses = socket.getaddrinfo(parsed.hostname, None)
        resolved = []
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if (
                not ip.is_loopback
                and not ip.is_global
                and not any(ip in network for network in _ALLOWED_NON_GLOBAL_NETWORKS)
            ):
                return False, f"URL resolves to blocked address: {ip}", None
            resolved.append(str(ip))
        return True, "", resolved[0]
    except Exception as exc:
        return False, str(exc), None


def _safe(url: str) -> tuple[bool, str]:
    safe, reason, _ = _resolve(url)
    return safe, reason


def _jsonable(value: Any) -> Any:
    for name in ("model_dump", "dict", "to_dict"):
        method = getattr(value, name, None)
        if callable(method):
            with contextlib.suppress(Exception):
                return method()
    data = getattr(value, "data", None)
    if data is not None and data is not value:
        return _jsonable(data)
    return (
        value if isinstance(value, (dict, list, str, int, float, bool, type(None))) else str(value)
    )


def _result(value: Any) -> Any:
    value = _jsonable(value)
    for _ in range(3):
        if isinstance(value, dict) and "result" in value:
            return value["result"]
        if isinstance(value, dict) and isinstance(value.get("data"), dict):
            value = value["data"]
            continue
        break
    return value


async def _proxy_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await reader.readuntil(b"\r\n\r\n")
        first = request.split(b"\r\n", 1)[0].decode()
        method, target, _ = first.split(" ", 2)
        parsed = urlparse(f"https://{target}" if method == "CONNECT" else target)
        if parsed.scheme not in {"http", "https"}:
            raise RuntimeError("unsupported proxy request")
        safe, reason, resolved = _resolve(parsed.geturl())
        if not safe or resolved is None:
            raise RuntimeError(reason)
        remote_reader, remote_writer = await asyncio.open_connection(
            resolved, parsed.port or (443 if method == "CONNECT" else 80)
        )
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            async def pipe(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()

            await asyncio.gather(pipe(reader, remote_writer), pipe(remote_reader, writer))
        else:
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            lines = request.split(b"\r\n")
            lines[0] = f"{method} {path} HTTP/1.1".encode()
            remote_writer.write(b"\r\n".join(lines))
            await remote_writer.drain()
            while chunk := await remote_reader.read(65536):
                writer.write(chunk)
                await writer.drain()
        remote_writer.close()
        await remote_writer.wait_closed()
    except Exception as exc:
        writer.write(
            f"HTTP/1.1 403 Forbidden\r\nContent-Length: {len(str(exc))}\r\n\r\n{exc}".encode()
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _proxy() -> int:
    global _PROXY, _PROXY_PORT
    if _PROXY is None:
        _PROXY = await asyncio.start_server(_proxy_client, "0.0.0.0", 0)
        _PROXY_PORT = _PROXY.sockets[0].getsockname()[1]
    assert _PROXY_PORT is not None
    return _PROXY_PORT


async def _close() -> bool:
    global _CLIENT, _SESSION, _PROXY, _PROXY_PORT
    existed = _SESSION is not None
    if _SESSION is not None:
        with contextlib.suppress(Exception):
            await _SESSION.end()
    if _CLIENT is not None:
        with contextlib.suppress(Exception):
            await _CLIENT.close()
    if _PROXY is not None:
        _PROXY.close()
        await _PROXY.wait_closed()
    _CLIENT = _SESSION = _PROXY = None
    _PROXY_PORT = None
    return existed


async def _session(request: dict[str, Any]) -> Any:
    global _CLIENT, _SESSION
    if _SESSION is None:
        await _proxy()
        proxy_url = request.get("proxy_url")
        if not isinstance(proxy_url, str) or not proxy_url:
            raise RuntimeError("no reachable proxy endpoint")
        _CLIENT = AsyncStagehand(
            server="local",
            model_api_key=os.environ.get("MODEL_API_KEY", "proxy-injected"),
            local_headless=request.get("headless", True),
            local_chrome_path=os.environ.get("STAGEHAND_LOCAL_CHROME_PATH", "/usr/bin/chromium"),
        )
        _SESSION = await _CLIENT.sessions.start(
            model_name=request["model_name"],
            browser={
                "type": "local",
                "launch_options": {
                    "headless": request.get("headless", True),
                    "executable_path": os.environ.get(
                        "STAGEHAND_LOCAL_CHROME_PATH", "/usr/bin/chromium"
                    ),
                    "args": [f"--proxy-server={proxy_url}"],
                },
            },
        )
    return _SESSION


async def _handle(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation == "health":
        return {"success": True}
    if operation == "proxy":
        return {"success": True, "port": await _proxy()}
    if operation == "close":
        return {"success": True, "closed": await _close()}
    if operation == "navigate":
        url = request.get("url", "")
        safe, reason = _safe(url)
        if not safe:
            return {"success": False, "error": f"browser_navigate blocked: {reason}"}
        try:
            session = await _session(request)
        except RuntimeError as exc:
            if str(exc) == "no reachable proxy endpoint":
                return {
                    "success": False,
                    "error": "browser automation is unavailable in this sandbox: no reachable proxy endpoint",
                }
            raise
        await session.navigate(url=url)
        return {"success": True, "url": url, "session_id": session.id}
    if _SESSION is None:
        return {"success": False, "error": "No active browser. Call browser_navigate first."}
    if operation == "act":
        value = await _SESSION.act(input=request["action"])
        return {"success": True, "result": _result(value)}
    if operation == "observe":
        value = await _SESSION.observe(instruction=request["instruction"])
        return {"success": True, "observations": _result(value)}
    if operation == "extract":
        kwargs = {"instruction": request["instruction"]}
        if request.get("schema") is not None:
            kwargs["schema"] = request["schema"]
        value = await _SESSION.extract(**kwargs)
        return {"success": True, "data": _result(value)}
    return {"success": False, "error": "unknown browser operation"}


async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = json.loads((await reader.readline()).decode())
        response = await _handle(request)
    except Exception as exc:
        response = {"success": False, "error": str(exc)}
    writer.write(json.dumps(response).encode() + b"\n")
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _serve(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    server = await asyncio.start_unix_server(_client, path)
    os.chmod(path, 0o600)
    async with server:
        await server.serve_forever()


async def _request(path: str, encoded: str) -> None:
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(base64.urlsafe_b64decode(encoded.encode()) + b"\n")
    await writer.drain()
    response = await reader.readline()
    writer.close()
    await writer.wait_closed()
    print(response.decode().strip())


if __name__ == "__main__":
    asyncio.run(
        _serve(sys.argv[2]) if sys.argv[1] == "serve" else _request(sys.argv[2], sys.argv[3])
    )
