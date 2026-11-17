"""The dashboard reads a thread's history from the runtime the thread runs on."""

import json

import httpx2
import pytest

from openswe.threads import index, proxy, runtimes

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "00000000-0000-4000-8000-000000000001"


class _Mda:
    """The MDA deployment, as its read client reaches it: each read gets one answer."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, status: int = 200, content: bytes = b"[]"
    ) -> None:
        self.requests: list[httpx2.Request] = []
        self._answer = httpx2.Response(
            status, content=content, headers={"content-type": "application/json"}
        )
        runtime = runtimes.MdaRuntime("https://mda.test")
        # MDA has the backend's own thread already.
        runtime._mda_threads.add(THREAD)
        monkeypatch.setattr(runtimes.Runtimes, "mda_runtime", property(lambda _: runtime))
        monkeypatch.setattr(
            runtimes.MdaRuntime,
            "read_http",
            property(
                lambda _: httpx2.AsyncClient(
                    base_url=runtime.url, transport=httpx2.MockTransport(self._read)
                )
            ),
        )

    def _read(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._answer


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (200, b'[{"values": {"messages": []}, "next": []}]'),
        (404, b'{"detail": "Thread not found"}'),
    ],
)
async def test_an_mda_threads_history_is_mdas_answer(
    monkeypatch: pytest.MonkeyPatch, status: int, content: bytes
) -> None:
    mda = _Mda(monkeypatch, status=status, content=content)
    await index.create(THREAD, runtime="mda", metadata={"owner_login": "ada"})

    reply = await proxy.proxy_dashboard_thread_history(THREAD, "ada", b'{"limit": 20}')

    assert reply == (status, content, "application/json")
    [request] = mda.requests
    assert (request.method, request.url.path) == ("POST", f"/threads/{THREAD}/history")
    # Without a checkpoint to page from, the dashboard reads at most five.
    assert json.loads(request.content) == {"limit": 5}


async def test_a_local_threads_history_comes_from_the_agent_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mda = _Mda(monkeypatch)
    agent_server: list[tuple[str, str]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        agent_server.append((request.method, request.url.path))
        return httpx2.Response(200, content=b"[]", headers={"content-type": "application/json"})

    monkeypatch.setattr(proxy, "pooled_transport", lambda: httpx2.MockTransport(answer))
    # A local thread is one made before every new thread ran on MDA.
    await index.create(THREAD, runtime="local", metadata={"owner_login": "ada"})

    history = await proxy.proxy_dashboard_thread_history(THREAD, "ada", b"{}")

    assert history == (200, b"[]", "application/json")
    assert agent_server == [("POST", f"/threads/{THREAD}/history")]
    assert mda.requests == []
