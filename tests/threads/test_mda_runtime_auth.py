"""The backend reads MDA as LangSmith Studio does, and writes only as itself."""

import httpx
import pytest
from langgraph_sdk.client import LangGraphClient

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
    # Nothing presents the old trusted-service secret to MDA any more.
    assert "x-mda-service-secret" not in mda.http.headers
    assert "x-mda-service-secret" not in mda.read_http.headers


async def test_the_sdk_reads_as_studio_and_writes_as_the_backend(
    mda: runtimes.MdaRuntime,
) -> None:
    reads = mda.read_sdk().http.client.headers
    writes = mda.sdk().http.client.headers
    assert reads["x-auth-scheme"] == "langsmith"
    assert "x-auth-scheme" not in writes
    assert reads["x-api-key"] == writes["x-api-key"] == "lsv2_test"


async def test_thread_reads_and_writes_take_their_own_clients(
    mda: runtimes.MdaRuntime,
) -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("x-auth-scheme")))
        return httpx.Response(200, json={"thread_id": "t1", "metadata": {}, "values": {}})

    def client(sdk_client: LangGraphClient) -> LangGraphClient:
        http = sdk_client.http.client
        sdk_client.http.client = httpx.AsyncClient(
            base_url=http.base_url, headers=http.headers, transport=httpx.MockTransport(handler)
        )
        return sdk_client

    threads = runtimes._SdkThreads(client(mda.sdk()), client(mda.read_sdk()))

    await threads.get("t1")
    await threads.update("t1", {"title": "x"})

    assert seen == [
        ("GET", "/threads/t1", "langsmith"),
        ("PATCH", "/threads/t1", None),
    ]
