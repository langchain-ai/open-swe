import json
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import JsonValue
from starlette.types import Message, Receive, Scope, Send

from openswe.jobs import queue
from openswe.remote_runtime import proxy
from openswe.remote_runtime.proxy import MdaRuntime
from openswe.remote_runtime.tokens import RemoteRun, sign_runtime_token, verify_runtime_token
from openswe.threads import index, router
from openswe.threads.index import Runtime, ThreadStatus

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "00000000-0000-4000-8000-000000000001"


class _Http:
    def __init__(self, threads: _Threads) -> None:
        self._threads = threads

    async def post(self, path: str, *, json: dict[str, JsonValue]) -> dict[str, JsonValue]:
        source = path.split("/")[2]
        copy_id = f"{source[:-4]}copy"
        self._threads.threads[copy_id] = {**self._threads.threads[source], "thread_id": copy_id}
        return self._threads.threads[copy_id]


class _Client:
    def __init__(self, threads: _Threads) -> None:
        self.http = _Http(threads)


class _Threads:
    """One runtime's threads, as the router copies them."""

    def __init__(self) -> None:
        self.threads: dict[str, dict[str, JsonValue]] = {}
        self.client = _Client(self)
        self.reachable = True

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        return self.threads.get(thread_id)

    async def indexed(self, thread_id: str) -> index.IndexedThread | None:
        if not self.reachable:
            raise httpx.ConnectError("Agent Server unreachable")
        thread = self.threads.get(thread_id)
        return None if thread is None else index.IndexedThread.from_agent_server(thread)

    async def create(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        now = datetime.now(UTC).isoformat()
        self.threads.setdefault(
            thread_id,
            {
                "thread_id": thread_id,
                "metadata": dict(metadata),
                "status": "idle",
                "values": {},
                "created_at": now,
                "updated_at": now,
            },
        )

    async def update(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        thread = self.threads[thread_id]
        current = thread["metadata"]
        thread["metadata"] = {**(current if isinstance(current, dict) else {}), **metadata}
        thread["updated_at"] = datetime.now(UTC).isoformat()

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
            return {"graph": assistant_id, "__run_token": sign_runtime_token(run)}

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
    assert isinstance(context, dict) and isinstance(token := context["__run_token"], str)
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


@pytest.mark.parametrize(("assistant_id", "moves"), [("agent", True), ("review-scout", False)])
async def test_mda_all_threads_moves_every_agent_thread_but_no_other_graph(
    monkeypatch: pytest.MonkeyPatch, secret: None, assistant_id: str, moves: bool
) -> None:
    monkeypatch.setenv("MDA_ALL_THREADS", "true")
    harness = _Harness(monkeypatch, chose_mda=False)
    await harness.request("POST", "/threads", {"thread_id": THREAD, "metadata": {}})

    await harness.request("POST", f"/threads/{THREAD}/runs", {"assistant_id": assistant_id})

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == ("mda" if moves else "local")
    assert bool(harness.forwarded) is moves


async def test_a_first_state_write_moves_a_fresh_thread_to_mda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_ALL_THREADS", "true")
    harness = _Harness(monkeypatch)
    await harness.request(
        "POST", "/threads", {"thread_id": THREAD, "metadata": {"graph_id": "agent"}}
    )

    await harness.request("POST", f"/threads/{THREAD}/state", {"values": {"messages": []}})

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"
    assert harness.runtimes.mda.threads[THREAD]["metadata"] == {
        "graph_id": "open-swe",
        "runtime": "mda",
    }
    [(path, _)] = harness.forwarded
    assert path == f"/threads/{THREAD}/state"


async def test_a_copy_of_an_mda_thread_is_indexed_on_mda(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda", metadata={"title": "source"})
    await harness.runtimes.mda.create(THREAD, {"title": "source"})

    status, made = await harness.request("POST", f"/threads/{THREAD}/copy", {})

    assert status == 200 and isinstance(made, dict)
    copy = await index.get(str(made["thread_id"]))
    assert copy is not None and copy.runtime == "mda" and copy.metadata == {"title": "source"}
    assert copy.thread_id in harness.runtimes.local.threads


async def test_with_dual_writes_each_agent_server_change_reaches_the_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    local = harness.runtimes.local

    async def agent_server(scope: Scope, receive: Receive, send: Send) -> None:
        """The Agent Server, answering thread calls as the index is kept beside it."""
        message = await receive()
        body = json.loads(message.get("body") or b"{}")
        path, method = scope["path"], scope["method"]
        answer: JsonValue = {}
        if path == "/threads":
            await local.create(body["thread_id"], body["metadata"])
            answer = local.threads[body["thread_id"]]
        elif method == "PATCH":
            await local.update(THREAD, body["metadata"])
            answer = local.threads[THREAD]
        elif method == "DELETE":
            await local.delete(THREAD)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": json.dumps(answer).encode()})

    harness.router = router.ThreadIndexMirror(agent_server)

    await harness.request("POST", "/threads", {"thread_id": THREAD, "metadata": {"o": "ada"}})
    await harness.request("PATCH", f"/threads/{THREAD}", {"metadata": {"title": "Fix it"}})
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.metadata == {"o": "ada", "title": "Fix it"}

    # MDA's proxy claims the thread on the Agent Server itself, then starts the run there.
    await local.update(THREAD, {"runtime": "mda"})
    await harness.request("POST", f"/threads/{THREAD}/runs", {"assistant_id": "agent"})
    started = await index.get(THREAD)
    assert started is not None and (started.runtime, started.status) == ("mda", "busy")

    await harness.request("DELETE", f"/threads/{THREAD}")
    assert await index.get(THREAD) is None


async def test_with_dual_writes_a_failed_copy_is_retried_from_a_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    local = harness.runtimes.local
    await local.create(THREAD, {"title": "Fix it"})

    async def agent_server(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    harness.router = router.ThreadIndexMirror(agent_server)
    local.reachable = False
    await harness.request("PATCH", f"/threads/{THREAD}", {"metadata": {"title": "Fix it"}})
    assert await index.get(THREAD) is None

    local.reachable = True
    [retry] = await queue.find({})
    assert retry.payload == {"thread_id": THREAD}
    await router.sync_thread(THREAD)
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.metadata == {"title": "Fix it"}


@pytest.mark.parametrize(("permissions", "from_index"), [([], False), (["openswe:backend"], True)])
async def test_under_custom_auth_only_the_backend_is_answered_from_the_index(
    monkeypatch: pytest.MonkeyPatch, permissions: list[str], from_index: bool
) -> None:
    from langgraph_api import config
    from langgraph_api.auth import middleware as auth
    from starlette.authentication import AuthCredentials, AuthenticationBackend, SimpleUser
    from starlette.middleware import Middleware
    from starlette.requests import HTTPConnection

    class SignedIn(AuthenticationBackend):
        async def authenticate(self, conn: HTTPConnection) -> tuple[AuthCredentials, SimpleUser]:
            return AuthCredentials(permissions), SimpleUser("ada")

    monkeypatch.setattr(config, "USES_CUSTOM_AUTH", True)
    monkeypatch.setattr(
        auth,
        "auth_middleware",
        Middleware(
            auth.ConditionalAuthenticationMiddleware, backend=SignedIn(), on_error=auth.on_error
        ),
    )
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="local", metadata={"owner_login": "bob"})

    _, read = await harness.request("GET", f"/threads/{THREAD}")

    # Anyone else's call reaches the Agent Server, whose rules show ada only her own threads.
    assert harness.reached_here == ([] if from_index else [f"/threads/{THREAD}"])
    assert (isinstance(read, dict) and read.get("metadata") == {"owner_login": "bob"}) is from_index
