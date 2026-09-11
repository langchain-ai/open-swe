"""Isolated processes and databases for a disposable validation PR."""

import asyncio
import json
import os
import secrets
import signal
import socket
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx2
import jwt
import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
async def cutover_database():
    if os.environ.get("RUN_ANALYTICS_CUTOVER_VALIDATION") != "1":
        pytest.skip("temporary cutover rehearsal is opt-in")
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.fail("cutover validation requires TEST_ANALYTICS_POSTGRES_URI")
    engine = create_async_engine(uri, isolation_level="AUTOCOMMIT")
    name = f"cutover_validation_{uuid4().hex}"
    async with engine.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield make_url(uri).set(database=name).render_as_string(hide_password=False)
    finally:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await engine.dispose()


class Deployment:
    def __init__(self, path: Path, uri: str):
        self.path = path
        self.uri = uri
        self.process = None
        self.log = None
        self.generation = 0
        self.signing_key = secrets.token_hex(32)
        self.webhook_key = secrets.token_hex(32)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        now = int(time.time())
        session = jwt.encode(
            {"sub": "cutover-reader", "iat": now, "exp": now + 3600},
            self.signing_key,
            algorithm="HS256",
        )
        self.http = httpx2.AsyncClient(
            base_url=self.url, cookies={"osw_session": session}, timeout=10
        )

    async def start(self, phase: str):
        self.path.mkdir(parents=True, exist_ok=True)
        config = {
            "dependencies": [str(REPO)],
            "graphs": {"probe": "tests.cutover_validation.harness:graph"},
            "http": {"app": "tests.cutover_validation.harness:app"},
        }
        config_path = self.path / "langgraph.json"
        config_path.write_text(json.dumps(config))
        self.generation += 1
        log_path = self.path / f"{self.generation}-{phase}.log"
        self.log = log_path.open("w")
        env = {key: os.environ[key] for key in ("PATH", "HOME", "SYSTEMROOT") if key in os.environ}
        env.update(
            PYTHONPATH=str(REPO),
            PYTHONUNBUFFERED="1",
            CUTOVER_PHASE=phase,
            POSTGRES_URI=self.uri,
            LANGGRAPH_URL=self.url,
            DASHBOARD_BASE_URL=self.url,
            DASHBOARD_JWT_SECRET=self.signing_key,
            GITHUB_WEBHOOK_SECRET=self.webhook_key,
            ALLOWED_GITHUB_USERS="cutover-reader",
            CONFIGURED_ADMINS="cutover-reader",
            SANDBOX_TYPE="modal",
            LANGSMITH_TRACING="false",
            LANGCHAIN_TRACING_V2="false",
            LANGGRAPH_API_LOG_LEVEL="WARNING",
        )
        self.process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("langgraph")),
            "dev",
            "--no-browser",
            "--no-reload",
            "--port",
            str(self.port),
            "--config",
            str(config_path),
            cwd=self.path,
            env=env,
            stdout=self.log,
            stderr=self.log,
            start_new_session=True,
        )
        for _ in range(120):
            if self.process.returncode is not None:
                pytest.fail(f"server exited; log: {log_path}\n{log_path.read_text()}")
            try:
                response = await self.http.get("/ok")
                if response.status_code == 200:
                    return
            except httpx2.TransportError:
                pass
            await asyncio.sleep(0.25)
        pytest.fail(f"server startup timed out; log: {log_path}\n{log_path.read_text()}")

    async def stop(self):
        if self.process is not None and self.process.returncode is None:
            os.killpg(self.process.pid, signal.SIGINT)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=20)
            except TimeoutError:
                os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        if self.log is not None:
            self.log.close()

    async def json(self, method: str, path: str, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        assert response.is_success, (path, response.status_code, response.text)
        return response.json() if response.content else None

    async def report(self):
        return await self.json("GET", "/dashboard/api/agent-usage-leaderboard?period=all")

    async def readiness(self):
        return await self.json("GET", "/dashboard/api/analytics/readiness")

    async def wait_for(self, query, predicate, *, timeout=30):
        deadline = time.monotonic() + timeout
        value = None
        while time.monotonic() < deadline:
            value = await query()
            if predicate(value):
                return value
            await asyncio.sleep(0.25)
        pytest.fail(f"Expected state did not arrive: {value}")


@pytest.fixture
async def deployment(tmp_path, cutover_database):
    instance = Deployment(tmp_path / "deployment", cutover_database)
    try:
        yield instance
    finally:
        await instance.stop()
        await instance.http.aclose()
