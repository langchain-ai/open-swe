"""Isolate the >32 MiB upload rejection behaviour of the tunnel edge."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

SPIKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_DIR.parent.parent
LOG_DIR = REPO_ROOT / "logs"
MIB = 1024 * 1024
PORT = int(os.environ.get("SPIKE_PORT", "47001"))
CASES = [(33 * MIB, "33 MiB"), (32 * MIB + 1, "32 MiB + 1 byte"), (32 * MIB, "32 MiB exactly")]


async def main() -> int:
    endpoint = os.environ["LANGSMITH_ENDPOINT"].rstrip("/")
    headers = {"X-Api-Key": os.environ["LANGSMITH_API_KEY"]}

    server_log = (LOG_DIR / "tunnel-server.log").open("ab")
    server = subprocess.Popen(
        ["node", str(SPIKE_DIR / "server.mjs")],
        env={**os.environ, "SPIKE_PORT": str(PORT)},
        stdout=server_log,
        stderr=server_log,
    )
    await asyncio.sleep(1.5)

    connection_id = ""
    connector: subprocess.Popen[bytes] | None = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240.0)) as client:
            created = await client.post(
                f"{endpoint}/v1/connections",
                json={"name": f"workstation-spike-{uuid.uuid4().hex[:8]}"},
                headers=headers,
            )
            created.raise_for_status()
            connection_id = str(created.json()["id"])
            print(f"connection id={connection_id}", flush=True)

            connector_log = (LOG_DIR / "tunnel-connector.log").open("ab")
            connector = subprocess.Popen(
                [str(SPIKE_DIR / "langsmith-connector")],
                env={
                    **os.environ,
                    "LANGSMITH_CONNECTION_ID": connection_id,
                    "LANGSMITH_CONNECTOR_TARGETS": f"workstation=http://127.0.0.1:{PORT}",
                },
                stdout=connector_log,
                stderr=connector_log,
            )

            tunnel = ""
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                response = await client.get(
                    f"{endpoint}/v1/connections/{connection_id}/targets", headers=headers
                )
                for item in response.json().get("items", []):
                    if item.get("is_available") and item.get("tunnel_url"):
                        tunnel = str(item["tunnel_url"])
                if tunnel:
                    break
                await asyncio.sleep(1.0)
            print(f"tunnel ready={bool(tunnel)}", flush=True)

            for size, label in CASES:
                payload = b"x" * size
                start = time.monotonic()
                try:
                    response = await client.post(
                        f"{tunnel}/upload",
                        headers={**headers, "Content-Type": "application/octet-stream"},
                        content=payload,
                    )
                    print(
                        f"{label}: status={response.status_code} "
                        f"body={response.text[:300]!r} ({time.monotonic() - start:.1f}s)",
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"{label}: EXCEPTION {type(exc).__name__}: {exc!r} "
                        f"({time.monotonic() - start:.1f}s)",
                        flush=True,
                    )
    finally:
        if connector is not None:
            connector.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                connector.wait(timeout=10)
        server.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            server.wait(timeout=10)
        if connection_id:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                deleted = await client.delete(
                    f"{endpoint}/v1/connections/{connection_id}", headers=headers
                )
                print(f"DELETED connection {connection_id} -> {deleted.status_code}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
