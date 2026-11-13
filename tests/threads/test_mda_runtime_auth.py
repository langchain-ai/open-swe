"""The backend reads MDA as LangSmith Studio does, and writes only as itself."""

import asyncio
from datetime import UTC, datetime

import httpx
import httpx2
import pytest

from openswe.threads import runtimes
from openswe.utils.http_pool import close_pool


@pytest.fixture
def mda(monkeypatch: pytest.MonkeyPatch) -> runtimes.MdaRuntime:
    monkeypatch.setenv("MDA_RUNTIME_API_KEY", "lsv2_test")
    return runtimes.MdaRuntime("http://mda.test")


async def test_reads_say_studio_and_writes_do_not(mda: runtimes.MdaRuntime) -> None:
    assert mda.read_http.headers["x-auth-scheme"] == "langsmith"
    assert mda.read_http.headers["x-api-key"] == "lsv2_test"
    assert "x-auth-scheme" not in mda.http.headers
    assert mda.http.headers["x-api-key"] == "lsv2_test"


async def test_the_sdk_writes_as_the_backend(mda: runtimes.MdaRuntime) -> None:
    writes = mda.sdk().http.client.headers
    assert "x-auth-scheme" not in writes
    assert writes["x-api-key"] == "lsv2_test"


async def test_thread_reads_and_writes_take_their_own_clients(
    mda: runtimes.MdaRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-auth-scheme")))
        if request.url.path in {"/threads/search", "/threads/t1/runs"}:
            return httpx2.Response(200, json=[])
        return httpx2.Response(200, json={"thread_id": "t1", "metadata": {}, "values": {}})

    def write(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-auth-scheme")))
        assert request.headers["Prefer"] == "return=minimal"
        return httpx.Response(204)

    headers = mda.read_http.headers
    threads = runtimes._MdaThreads(mda)
    monkeypatch.setattr(
        runtimes.MdaRuntime,
        "read_http",
        property(
            lambda self: httpx2.AsyncClient(
                base_url=mda.url, headers=headers, transport=httpx2.MockTransport(handler)
            )
        ),
    )
    threads.client.http.client = httpx.AsyncClient(
        base_url=mda.url,
        headers=threads.client.http.client.headers,
        transport=httpx.MockTransport(write),
    )

    await threads.get("t1")
    await threads.indexed("t1")
    await threads.state("t1")
    assert not await threads.has_active_run("t1")
    await threads.update("t1", {"title": "x"})

    assert seen == [
        ("GET", "/threads/t1", "langsmith"),
        ("POST", "/threads/search", "langsmith"),
        ("GET", "/threads/t1/state", "langsmith"),
        ("GET", "/threads/t1/runs", "langsmith"),
        ("GET", "/threads/t1/runs", "langsmith"),
        ("PATCH", "/threads/t1", None),
    ]


async def test_a_missing_thread_reads_as_none(
    mda: runtimes.MdaRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads = runtimes._MdaThreads(mda)
    monkeypatch.setattr(
        runtimes.MdaRuntime,
        "read_http",
        property(
            lambda self: httpx2.AsyncClient(
                base_url=mda.url, transport=httpx2.MockTransport(lambda _: httpx2.Response(404))
            )
        ),
    )

    assert await threads.get("t1") is None
    assert await threads.latest_run("t1") is None


@pytest.mark.parametrize("queued_status", ["pending", "interrupted"])
async def test_latest_work_run_keeps_the_live_run_ahead_of_a_queued_follow_up(
    mda: runtimes.MdaRuntime, queued_status: runtimes.RunStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = runtimes.MdaRun(run_id="live", status="running", created_at=datetime.now(UTC))
    queued = runtimes.MdaRun(run_id="follow-up", status=queued_status, created_at=datetime.now(UTC))
    threads = runtimes._MdaThreads(mda)
    monkeypatch.setattr(
        runtimes.MdaRuntime,
        "read_http",
        property(
            lambda self: httpx2.AsyncClient(
                base_url=mda.url,
                transport=httpx2.MockTransport(
                    lambda request: httpx2.Response(
                        200,
                        json=[
                            (
                                live if request.url.params.get("status") == "running" else queued
                            ).model_dump(mode="json")
                        ],
                    )
                ),
            )
        ),
    )
    run = await threads.latest_run("work")
    assert run is not None and run.run_id == "live" and run.active


@pytest.mark.parametrize("bypass", ["127.0.0.1", "*.0.0.1"])
async def test_mda_pools_reuse_isolated_connections_until_shutdown(
    monkeypatch: pytest.MonkeyPatch, bypass: str
) -> None:
    monkeypatch.setenv("NO_PROXY", bypass)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    accepted = 0
    closed: asyncio.Queue[None] = asyncio.Queue()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted
        accepted += 1
        try:
            while await reader.readuntil(b"\r\n\r\n"):
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            closed.put_nowait(None)

    async with await asyncio.start_server(serve, "127.0.0.1", 0) as server:
        runtime = runtimes.MdaRuntime(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        try:
            for _ in range(2):
                async with runtime.http as client:
                    assert (await client.get("/")).status_code == 200
                async with runtime.read_http as client:
                    assert (await client.get("/")).status_code == 200
                async with runtime.stream_http as client:
                    async with client.stream("GET", "/") as response:
                        assert await response.aread() == b"{}"
            assert accepted == 3
            await close_pool()
            for _ in range(3):
                await asyncio.wait_for(closed.get(), 2)
            async with runtime.http as client:
                assert (await client.get("/")).status_code == 200
            assert accepted == 4
        finally:
            await close_pool()
            await asyncio.wait_for(closed.get(), 2)
