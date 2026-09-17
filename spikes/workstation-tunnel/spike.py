"""Workstation tunnel de-risking spike.

Creates a throwaway LangSmith connection, runs a local connector against a
loopback Node server, and exercises streaming, large upload/download, WebSocket
upgrade, and connector-restart behavior through the tunnel.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
import websockets

SPIKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_DIR.parent.parent
LOG_DIR = REPO_ROOT / "logs"
CONNECTOR_BIN = SPIKE_DIR / "langsmith-connector"
SERVER_JS = SPIKE_DIR / "server.mjs"

LOCAL_PORT = int(os.environ.get("SPIKE_PORT", "47000"))
TARGET_NAME = "workstation"

MIB = 1024 * 1024


@dataclass
class Result:
    name: str
    status: Literal["PASS", "FAIL"]
    seconds: float
    detail: str = ""
    notes: list[str] = field(default_factory=list)


RESULTS: list[Result] = []


def out(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def record(
    name: str, status: Literal["PASS", "FAIL"], seconds: float, detail: str = "", notes: list[str] | None = None
) -> None:
    result = Result(name=name, status=status, seconds=seconds, detail=detail, notes=notes or [])
    RESULTS.append(result)
    out(f"{name}: {status} ({seconds:.1f}s) {detail}")


class Management:
    def __init__(self, endpoint: str, api_key: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

    async def create(self, client: httpx.AsyncClient, name: str) -> str:
        response = await client.post(
            f"{self.endpoint}/v1/connections", json={"name": name}, headers=self._headers
        )
        response.raise_for_status()
        return str(response.json()["id"])

    async def targets(self, client: httpx.AsyncClient, connection_id: str) -> list[dict[str, object]]:
        response = await client.get(
            f"{self.endpoint}/v1/connections/{connection_id}/targets", headers=self._headers
        )
        response.raise_for_status()
        body = response.json()
        items = body.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    async def delete(self, client: httpx.AsyncClient, connection_id: str) -> str:
        response = await client.delete(
            f"{self.endpoint}/v1/connections/{connection_id}", headers=self._headers
        )
        return f"{response.status_code}"


class Connector:
    def __init__(self, endpoint: str, connection_id: str, log_path: Path) -> None:
        self._env = {
            **os.environ,
            "LANGSMITH_ENDPOINT": endpoint,
            "LANGSMITH_CONNECTION_ID": connection_id,
            "LANGSMITH_CONNECTOR_TARGETS": f"{TARGET_NAME}=http://127.0.0.1:{LOCAL_PORT}",
        }
        self._log_path = log_path
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        handle = self._log_path.open("ab")
        handle.write(f"\n==== connector start {time.strftime('%H:%M:%S')} ====\n".encode())
        handle.flush()
        self.process = subprocess.Popen(
            [str(CONNECTOR_BIN)], env=self._env, stdout=handle, stderr=handle
        )
        out(f"connector pid={self.process.pid}")

    def kill(self, sig: int = signal.SIGKILL) -> None:
        if self.process is None:
            return
        with contextlib.suppress(ProcessLookupError):
            self.process.send_signal(sig)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=10)
        self.process = None


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def wait_for_target(
    api: Management, client: httpx.AsyncClient, connection_id: str, timeout_s: float = 60.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    last = "no response"
    while time.monotonic() < deadline:
        try:
            items = await api.targets(client, connection_id)
        except httpx.HTTPError as exc:
            last = f"targets error: {exc!r}"
        else:
            last = json.dumps(items)
            for item in items:
                if item.get("name") == TARGET_NAME and item.get("is_available") and item.get("tunnel_url"):
                    return item
        await asyncio.sleep(1.0)
    raise RuntimeError(f"target never became available within {timeout_s}s; last: {last}")


async def t1_stream(tunnel: str, headers: dict[str, str], duration_s: int) -> tuple[bool, str, float, int]:
    start = time.monotonic()
    keepalives = 0
    exit_line: dict[str, object] | None = None
    header_at = -1.0
    try:
        timeout = httpx.Timeout(connect=30.0, read=duration_s + 20.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout, http2=False) as client:
            async with client.stream(
                "POST", f"{tunnel}/execute", headers=headers, json={"duration_s": duration_s}
            ) as response:
                header_at = time.monotonic() - start
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")[:500]
                    return False, f"status={response.status_code} body={body}", time.monotonic() - start, 0
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    kind = event.get("type")
                    if kind == "keepalive":
                        keepalives += 1
                        out(f"  T1 keepalive #{keepalives} at {time.monotonic() - start:.0f}s")
                    elif kind == "exit":
                        exit_line = event
                        break
    except Exception as exc:  # noqa: BLE001 - spike reports the exact failure
        return False, f"{type(exc).__name__}: {exc}", time.monotonic() - start, keepalives
    elapsed = time.monotonic() - start
    if exit_line is None:
        return False, f"stream ended without exit line (headers at {header_at:.1f}s)", elapsed, keepalives
    ok = abs(elapsed - duration_s) < 20 and keepalives >= max(1, duration_s // 10 - 1)
    return ok, f"exit={exit_line} keepalives={keepalives}", elapsed, keepalives


async def t2_upload(tunnel: str, headers: dict[str, str]) -> None:
    payload = os.urandom(30 * MIB)
    expected = sha256_of(payload)
    start = time.monotonic()
    notes: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            response = await client.post(
                f"{tunnel}/upload",
                headers={**headers, "Content-Type": "application/octet-stream"},
                content=payload,
            )
            body = response.text
            if response.status_code != 200:
                record("T2 upload 30 MiB", "FAIL", time.monotonic() - start, f"status={response.status_code} body={body[:400]}")
            else:
                got = response.json()
                ok = got.get("sha256") == expected and got.get("bytes") == len(payload)
                record(
                    "T2 upload 30 MiB",
                    "PASS" if ok else "FAIL",
                    time.monotonic() - start,
                    f"bytes={got.get('bytes')} sha256_match={got.get('sha256') == expected}",
                )
    except Exception as exc:  # noqa: BLE001
        record("T2 upload 30 MiB", "FAIL", time.monotonic() - start, f"{type(exc).__name__}: {exc}")

    over = os.urandom(33 * MIB)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
            response = await client.post(
                f"{tunnel}/upload",
                headers={**headers, "Content-Type": "application/octet-stream"},
                content=over,
            )
            notes.append(f"33 MiB -> status={response.status_code} body={response.text[:200]!r}")
            record(
                "T2b upload 33 MiB (expect 413)",
                "PASS" if response.status_code == 413 else "FAIL",
                time.monotonic() - start,
                f"status={response.status_code} body={response.text[:200]}",
            )
    except Exception as exc:  # noqa: BLE001
        record("T2b upload 33 MiB (expect 413)", "FAIL", time.monotonic() - start, f"{type(exc).__name__}: {exc}")


async def t3_download(tunnel: str, headers: dict[str, str]) -> None:
    total = 30 * MIB
    token = uuid.uuid4().hex[:8]
    start = time.monotonic()
    try:
        hasher = hashlib.sha256()
        received = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream(
                "GET", f"{tunnel}/download?bytes={total}&token={token}", headers=headers
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode(errors="replace")[:400]
                    record("T3 download 30 MiB", "FAIL", time.monotonic() - start, f"status={response.status_code} body={body}")
                    return
                async for chunk in response.aiter_bytes(256 * 1024):
                    received += len(chunk)
                    hasher.update(chunk)
            local = await client.get(f"{tunnel}/download-hash?token={token}", headers=headers)
            expected = local.json()
        elapsed = time.monotonic() - start
        ok = received == total and hasher.hexdigest() == expected.get("sha256")
        record(
            "T3 download 30 MiB",
            "PASS" if ok else "FAIL",
            elapsed,
            f"bytes={received}/{total} sha256_match={hasher.hexdigest() == expected.get('sha256')} "
            f"({received / MIB / max(elapsed, 0.001):.1f} MiB/s)",
        )
    except Exception as exc:  # noqa: BLE001
        record("T3 download 30 MiB", "FAIL", time.monotonic() - start, f"{type(exc).__name__}: {exc}")


async def t4_websocket(tunnel: str, api_key: str) -> None:
    ws_url = tunnel.replace("https://", "wss://", 1) + "/ws"
    start = time.monotonic()
    try:
        async with websockets.connect(
            ws_url, additional_headers={"X-Api-Key": api_key}, open_timeout=30
        ) as socket:
            await socket.send("hello")
            echoed = await asyncio.wait_for(socket.recv(), timeout=30)
        record(
            "T4 websocket echo",
            "PASS" if echoed == "hello" else "FAIL",
            time.monotonic() - start,
            f"echoed={echoed!r}",
        )
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        status = getattr(getattr(exc, "response", None), "status_code", None)
        body = getattr(getattr(exc, "response", None), "body", None)
        if status is not None:
            detail += f" | http_status={status} body={body!r}"
        record("T4 websocket echo", "FAIL", time.monotonic() - start, detail)


async def ping(tunnel: str, headers: dict[str, str]) -> tuple[int, str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        response = await client.get(f"{tunnel}/ping", headers=headers)
        return response.status_code, response.text[:300]


async def t5_reconnect(connector: Connector, tunnel: str, headers: dict[str, str]) -> None:
    down_observations: list[str] = []
    connector.kill()
    out("connector SIGKILLed")
    await asyncio.sleep(1.0)
    for _ in range(3):
        try:
            status, body = await ping(tunnel, headers)
            down_observations.append(f"status={status} body={body!r}")
        except Exception as exc:  # noqa: BLE001
            down_observations.append(f"{type(exc).__name__}: {exc}")
        await asyncio.sleep(1.0)

    connector.start()
    start = time.monotonic()
    deadline = start + 120
    last = "never attempted"
    while time.monotonic() < deadline:
        try:
            status, body = await ping(tunnel, headers)
            last = f"status={status} body={body!r}"
            if status == 200:
                record(
                    "T5 reconnect",
                    "PASS",
                    time.monotonic() - start,
                    f"ping OK after restart; while down: {down_observations[0] if down_observations else 'n/a'}",
                    notes=down_observations,
                )
                return
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(1.0)
    record("T5 reconnect", "FAIL", time.monotonic() - start, f"last={last}", notes=down_observations)


async def main() -> int:
    endpoint = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    api_key = os.environ.get("LANGSMITH_API_KEY", "")
    if not api_key:
        out("LANGSMITH_API_KEY is not set in the environment")
        return 2

    LOG_DIR.mkdir(exist_ok=True)
    connector_log = LOG_DIR / "tunnel-connector.log"
    server_log = LOG_DIR / "tunnel-server.log"

    out(f"endpoint={endpoint}")
    server_handle = server_log.open("ab")
    server_process = subprocess.Popen(
        ["node", str(SERVER_JS)],
        env={**os.environ, "SPIKE_PORT": str(LOCAL_PORT)},
        stdout=server_handle,
        stderr=server_handle,
    )
    await asyncio.sleep(1.5)

    api = Management(endpoint, api_key)
    headers = {"X-Api-Key": api_key}
    created: list[str] = []
    connector: Connector | None = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            local = await client.get(f"http://127.0.0.1:{LOCAL_PORT}/ping")
            out(f"local server /ping -> {local.status_code} {local.text}")

            name = f"workstation-spike-{uuid.uuid4().hex[:8]}"
            connection_id = await api.create(client, name)
            created.append(connection_id)
            out(f"connection name={name} id={connection_id}")

            connector = Connector(endpoint, connection_id, connector_log)
            connector.start()

            target = await wait_for_target(api, client, connection_id)
            tunnel = str(target["tunnel_url"])
            out(f"target ready kind={target.get('kind')} available={target.get('is_available')}")
            out(f"tunnel host suffix={tunnel.split('.', 1)[-1]}")

            status, body = await ping(tunnel, headers)
            out(f"tunnel /ping -> {status} {body}")
            if status != 200:
                record("T0 tunnel ping", "FAIL", 0.0, f"status={status} body={body}")
                return 1
            record("T0 tunnel ping", "PASS", 0.0, f"status={status}")

            ok, detail, elapsed, keepalives = await t1_stream(tunnel, headers, 300)
            record("T1 stream 300s", "PASS" if ok else "FAIL", elapsed, detail)
            if not ok:
                for fallback in (60, 35):
                    ok2, detail2, elapsed2, _ = await t1_stream(tunnel, headers, fallback)
                    record(f"T1b stream {fallback}s", "PASS" if ok2 else "FAIL", elapsed2, detail2)
                    if ok2:
                        break

            await t2_upload(tunnel, headers)
            await t3_download(tunnel, headers)
            await t4_websocket(tunnel, api_key)
            await t5_reconnect(connector, tunnel, headers)
    except Exception as exc:  # noqa: BLE001
        out(f"FATAL: {type(exc).__name__}: {exc}")
    finally:
        if connector is not None:
            connector.kill()
        server_process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            server_process.wait(timeout=10)
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for connection_id in created:
                try:
                    status = await api.delete(client, connection_id)
                    out(f"DELETED connection {connection_id} -> {status}")
                except Exception as exc:  # noqa: BLE001
                    out(f"DELETE FAILED {connection_id}: {type(exc).__name__}: {exc}")

    print("\n===== RESULTS =====", flush=True)
    for result in RESULTS:
        print(f"{result.name}\t{result.status}\t{result.seconds:.1f}s\t{result.detail}", flush=True)
        for note in result.notes:
            print(f"\t\tnote: {note}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
