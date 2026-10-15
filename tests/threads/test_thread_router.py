import json
from collections.abc import Mapping

import httpx
import pytest
from pydantic import JsonValue
from starlette.types import Message, Receive, Scope, Send

from openswe.remote_runtime import proxy
from openswe.remote_runtime.proxy import MdaRuntime
from openswe.remote_runtime.tokens import RemoteRun, sign_runtime_token, verify_runtime_token
from openswe.threads import index, router
from openswe.threads.index import Runtime, ThreadStatus

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "00000000-0000-4000-8000-000000000001"


class _Threads:
    """One runtime's threads, as the router copies them."""

    def __init__(self) -> None:
        self.threads: dict[str, dict[str, JsonValue]] = {}

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        return self.threads.get(thread_id)

    async def create(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        self.threads.setdefault(
            thread_id,
            {"thread_id": thread_id, "metadata": dict(metadata), "status": "idle", "values": {}},
        )

    async def update(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        thread = self.threads[thread_id]
        current = thread["metadata"]
        thread["metadata"] = {**(current if isinstance(current, dict) else {}), **metadata}

    async def delete(self, thread_id: str) -> None:
        self.threads.pop(thread_id, None)

    async def status(self, thread_id: str) -> ThreadStatus | None:
        thread = self.threads.get(thread_id)
        return None if thread is None else index.as_status(thread.get("status"))

    async def has_history(self, thread_id: str) -> bool:
        thread = self.threads.get(thread_id)
        return thread is not None and bool(thread.get("values"))


class _Runtimes:
    def __init__(self, mda_runtime: MdaRuntime) -> None:
        self.local = _Threads()
        self.mda = _Threads()
        self.mda_runtime = mda_runtime

    def runs_on(self, runtime: Runtime) -> _Threads:
        return self.mda if runtime == "mda" else self.local

    def copies(self, runtime: Runtime) -> list[_Threads]:
        return [self.local, self.mda] if runtime == "mda" else [self.local]


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, chose_mda: bool = False) -> None:
        self.forwarded: list[tuple[str, dict[str, JsonValue]]] = []
        self.reached_here: list[str] = []
        self.local_status = 200

        def mda(request: httpx.Request) -> httpx.Response:
            self.forwarded.append((request.url.path, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={"run_id": "remote-run"})

        mda_runtime = MdaRuntime("http://mda.test")
        mda_runtime._http = httpx.AsyncClient(
            base_url=mda_runtime.url, transport=httpx.MockTransport(mda)
        )
        mda_runtime._mda_threads.add(THREAD)
        self.runtimes = _Runtimes(mda_runtime)
        monkeypatch.setattr(router, "RUNTIMES", self.runtimes)

        async def runs_on_mda(
            _metadata: Mapping[str, JsonValue] | None, _configurable: Mapping[str, JsonValue]
        ) -> bool:
            return chose_mda

        async def context(
            configurable: dict[str, JsonValue], *, thread_id: str, assistant_id: str
        ) -> dict[str, JsonValue]:
            run = RemoteRun(
                thread_id=thread_id, assistant_id=assistant_id, configurable=configurable
            )
            return {"graph": assistant_id, "run_token": sign_runtime_token(run)}

        monkeypatch.setattr(router, "runs_on_mda", runs_on_mda)
        monkeypatch.setattr(proxy, "remote_run_context", context)
        monkeypatch.setattr(proxy, "get_tracer_project", lambda: "open-swe-test")

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            self.reached_here.append(scope["path"])
            await send({"type": "http.response.start", "status": self.local_status, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        self.router = router.ThreadRouter(app)

    async def request(
        self, method: str, path: str, body: JsonValue = None
    ) -> tuple[int, JsonValue]:
        payload = b"" if body is None else json.dumps(body).encode()
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        }
        await self.router(scope, receive, send)
        status = int(sent[0]["status"])
        chunks = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, json.loads(chunks) if chunks.strip() else None


@pytest.fixture
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "test-signing-key-0123456789abcdef")


async def test_threads_are_served_from_the_index_and_copied_to_their_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)

    status, created = await harness.request(
        "POST", "/threads", {"thread_id": THREAD, "metadata": {"owner_login": "ada"}}
    )
    assert status == 200 and isinstance(created, dict)
    assert created["metadata"] == {"owner_login": "ada"}
    assert (await harness.request("POST", "/threads", {"thread_id": THREAD}))[0] == 409

    await harness.request("PATCH", f"/threads/{THREAD}", {"metadata": {"title": "Fix it"}})
    _, read = await harness.request("GET", f"/threads/{THREAD}")
    _, found = await harness.request(
        "POST", "/threads/search", {"metadata": {"title": "Fix it"}, "select": ["thread_id"]}
    )
    _, counted = await harness.request(
        "POST", "/threads/count", {"metadata": {"owner_login": "ada"}}
    )

    assert isinstance(read, dict) and read["metadata"] == {"owner_login": "ada", "title": "Fix it"}
    assert found == [{"thread_id": THREAD}]
    assert counted == 1
    assert harness.runtimes.local.threads[THREAD]["metadata"] == read["metadata"]
    assert harness.reached_here == []


async def test_a_thread_only_the_agent_server_knows_is_indexed_on_first_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    harness.runtimes.local.threads[THREAD] = {
        "thread_id": THREAD,
        "metadata": {"runtime": "mda", "owner_login": "ada"},
        "status": "busy",
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-02T00:00:00+00:00",
    }

    status, read = await harness.request("GET", f"/threads/{THREAD}")

    assert status == 200 and isinstance(read, dict) and read["status"] == "busy"
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"
    assert indexed.created_at.isoformat() == "2026-10-01T00:00:00+00:00"
    assert (await harness.request("GET", "/threads/00000000-0000-4000-8000-00000000ffff"))[0] == 404


@pytest.mark.parametrize("runtime", ["local", "mda"])
async def test_a_run_goes_to_its_threads_runtime_and_marks_the_thread_busy(
    monkeypatch: pytest.MonkeyPatch, secret: None, runtime: Runtime
) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime=runtime, metadata={"owner_login": "ada"})
    run = {
        "assistant_id": "agent",
        "input": {},
        "config": {"configurable": {"github_login": "ada"}},
    }

    status, _ = await harness.request("POST", f"/threads/{THREAD}/runs", run)

    assert status == 200
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.status == "busy"
    if runtime == "local":
        assert harness.reached_here == [f"/threads/{THREAD}/runs"]
        assert harness.forwarded == []
    else:
        assert harness.reached_here == []
        [(path, sent)] = harness.forwarded
        assert path == f"/threads/{THREAD}/runs" and sent["assistant_id"] == "open-swe"


async def test_a_refused_run_leaves_the_thread_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness(monkeypatch)
    harness.local_status = 409
    await index.create(THREAD, runtime="local")

    await harness.request("POST", f"/threads/{THREAD}/runs", {"assistant_id": "agent"})

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.status == "idle"


async def test_a_fresh_thread_moves_to_mda_when_its_person_chose_it(
    monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    harness = _Harness(monkeypatch, chose_mda=True)
    await harness.request(
        "POST", "/threads", {"thread_id": THREAD, "metadata": {"owner_login": "ada"}}
    )
    run = {
        "assistant_id": "agent",
        "input": {},
        "config": {"configurable": {"github_login": "ada", "repo": {"owner": "a", "name": "b"}}},
    }

    await harness.request("POST", f"/threads/{THREAD}/runs", run)

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"
    assert indexed.metadata["runtime"] == "mda"
    assert harness.runtimes.mda.threads[THREAD]["metadata"] == indexed.metadata
    assert harness.runtimes.local.threads[THREAD]["metadata"] == indexed.metadata
    [(_, sent)] = harness.forwarded
    context = sent["context"]
    assert isinstance(context, dict) and isinstance(token := context["run_token"], str)
    assert verify_runtime_token(token).configurable["github_login"] == "ada"


async def test_deleting_a_thread_removes_every_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda")
    await harness.runtimes.local.create(THREAD, {})
    await harness.runtimes.mda.create(THREAD, {})

    status, _ = await harness.request("DELETE", f"/threads/{THREAD}")

    assert status == 204
    assert await index.get(THREAD) is None
    assert harness.runtimes.local.threads == {} and harness.runtimes.mda.threads == {}
