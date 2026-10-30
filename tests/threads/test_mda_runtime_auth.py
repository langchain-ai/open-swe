"""The backend reads MDA as LangSmith Studio does, and writes only as itself."""

import httpx
import pytest

from openswe.threads import runtimes


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
) -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-auth-scheme")))
        if request.url.path == "/threads/search":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"thread_id": "t1", "metadata": {}, "values": {}})

    def mocked(http: httpx.AsyncClient) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=http.base_url, headers=http.headers, transport=httpx.MockTransport(handler)
        )

    threads = runtimes._MdaThreads(mda)
    threads.reads = mocked(threads.reads)
    threads.client.http.client = mocked(threads.client.http.client)

    await threads.get("t1")
    await threads.indexed("t1")
    await threads.state("t1")
    await threads.update("t1", {"title": "x"})

    assert seen == [
        ("GET", "/threads/t1", "langsmith"),
        ("POST", "/threads/search", "langsmith"),
        ("GET", "/threads/t1/state", "langsmith"),
        ("PATCH", "/threads/t1", None),
    ]


async def test_a_missing_thread_reads_as_none(mda: runtimes.MdaRuntime) -> None:
    threads = runtimes._MdaThreads(mda)
    threads.reads = httpx.AsyncClient(
        base_url=mda.url, transport=httpx.MockTransport(lambda _: httpx.Response(404))
    )

    assert await threads.get("t1") is None
