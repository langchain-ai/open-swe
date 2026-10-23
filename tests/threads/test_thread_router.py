import json
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import JsonValue
from starlette.types import Message, Receive, Scope, Send

from openswe.agent_api.runs import run_config
from openswe.agent_api.tokens import RemoteRun, sign_runtime_token, verify_runtime_token
from openswe.threads import index, router, runtimes
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


class _Runtimes:
    def __init__(self, mda_runtime: runtimes.MdaRuntime) -> None:
        self.local = _Threads()
        self.mda = _Threads()
        self.mda_runtime = mda_runtime

    def runs_on(self, runtime: Runtime) -> _Threads:
        return self.mda if runtime == "mda" else self.local

    def copies(self, runtime: Runtime) -> list[_Threads]:
        return [self.local, self.mda] if runtime == "mda" else [self.local]


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.forwarded: list[tuple[str, dict[str, JsonValue]]] = []
        self.reached_here: list[str] = []
        self.local_status = 200

        def mda(request: httpx.Request) -> httpx.Response:
            self.forwarded.append((request.url.path, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={"run_id": "remote-run"})

        mda_runtime = runtimes.MdaRuntime("http://mda.test")
        mda_runtime._http = httpx.AsyncClient(
            base_url=mda_runtime.url, transport=httpx.MockTransport(mda)
        )
        mda_runtime._mda_threads.add(THREAD)
        self.runtimes = _Runtimes(mda_runtime)
        monkeypatch.setattr(router, "RUNTIMES", self.runtimes)

        async def context(
            configurable: dict[str, JsonValue],
            *,
            thread_id: str,
            assistant_id: str,
            metadata: dict[str, JsonValue] | None = None,
        ) -> dict[str, JsonValue]:
            run = RemoteRun(
                thread_id=thread_id,
                assistant_id=assistant_id,
                configurable=configurable,
                metadata=metadata or {},
            )
            return {"graph": assistant_id, "__run_token": sign_runtime_token(run)}

        monkeypatch.setattr(runtimes, "remote_run_context", context)
        monkeypatch.setattr(runtimes, "get_tracer_project", lambda: "open-swe-test")

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

        path, _, query = path.partition("?")
        scope: Scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query.encode(),
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
    assert created["metadata"] == {"owner_login": "ada", "runtime": "mda"}
    assert (await harness.request("POST", "/threads", {"thread_id": THREAD}))[0] == 409

    await harness.request("PATCH", f"/threads/{THREAD}", {"metadata": {"title": "Fix it"}})
    _, read = await harness.request("GET", f"/threads/{THREAD}")
    _, found = await harness.request(
        "POST", "/threads/search", {"metadata": {"title": "Fix it"}, "select": ["thread_id"]}
    )
    _, counted = await harness.request(
        "POST", "/threads/count", {"metadata": {"owner_login": "ada"}}
    )
    _, matched = await harness.request(
        "POST", "/threads/search?text=fix", {"select": ["thread_id"]}
    )
    _, missed = await harness.request("POST", "/threads/count?text=nope", {})

    assert isinstance(read, dict)
    assert read["metadata"] == {"owner_login": "ada", "title": "Fix it", "runtime": "mda"}
    assert found == [{"thread_id": THREAD}]
    assert isinstance(matched, list) and [thread["thread_id"] for thread in matched] == [THREAD]
    assert missed == 0
    assert counted == 1
    assert harness.runtimes.local.threads[THREAD]["metadata"] == read["metadata"]
    assert harness.runtimes.mda.threads[THREAD]["metadata"] == read["metadata"]
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


async def test_a_run_goes_to_mda_and_marks_the_thread_busy(
    monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda", metadata={"owner_login": "ada"})
    run = {
        "assistant_id": "agent",
        "input": {},
        "config": {"configurable": {"github_login": "ada"}},
    }

    status, _ = await harness.request("POST", f"/threads/{THREAD}/runs", run)

    assert status == 200
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.status == "busy"
    assert harness.reached_here == []
    [(path, sent)] = harness.forwarded
    assert path == f"/threads/{THREAD}/runs" and sent["assistant_id"] == "open-swe"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/runs", {"assistant_id": "agent"}),
        ("/runs/crons", {"assistant_id": "agent", "schedule": "* * * * *"}),
        ("/commands", {"id": 1, "method": "run.start", "params": {"assistant_id": "agent"}}),
        ("/commands", {"id": 1, "method": "input.respond", "params": {}}),
        ("/copy", {}),
    ],
)
async def test_a_local_thread_takes_no_new_activity(
    monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, JsonValue]
) -> None:
    harness = _Harness(monkeypatch)
    # A local thread is one made before every new thread ran on MDA.
    await index.create(THREAD, runtime="local")

    status, _ = await harness.request("POST", f"/threads/{THREAD}{path}", body)

    assert status == 410
    assert harness.reached_here == [] and harness.forwarded == []
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.status == "idle"
    assert await index.ThreadSearch().run() == [indexed]
    status, _ = await harness.request("POST", f"/threads/{THREAD}/history", {})
    assert status == 200 and harness.reached_here == [f"/threads/{THREAD}/history"]


# An async task worker's follow-ups run on the graph MDA launched it on.
_GRAPHS = pytest.mark.parametrize(
    ("metadata", "graph"),
    [
        pytest.param({}, "open-swe", id="agent"),
        pytest.param(
            {"mda_graph_id": "open-swe--general-purpose"}, "open-swe--general-purpose", id="worker"
        ),
        pytest.param({"mda_graph_id": "elsewhere"}, "open-swe", id="unknown-graph"),
    ],
)


@_GRAPHS
async def test_a_new_thread_runs_on_mda(
    monkeypatch: pytest.MonkeyPatch, secret: None, metadata: dict[str, JsonValue], graph: str
) -> None:
    harness = _Harness(monkeypatch)
    await harness.request(
        "POST", "/threads", {"thread_id": THREAD, "metadata": {"owner_login": "ada", **metadata}}
    )
    run = {
        "assistant_id": "agent",
        "input": {},
        "config": {
            "configurable": {
                "github_login": "ada",
                "repo": {"owner": "a", "name": "b"},
                "__event_streaming_v2": True,
            }
        },
        "metadata": {
            "kind": "task_message",
            "event_match_ids": ["match-1"],
            "ls_attribution_identifier": "trigger",
        },
    }

    await harness.request("POST", f"/threads/{THREAD}/runs", run)

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"
    assert indexed.metadata["runtime"] == "mda"
    assert harness.runtimes.mda.threads[THREAD]["metadata"] == indexed.metadata
    assert harness.runtimes.local.threads[THREAD]["metadata"] == indexed.metadata
    [(_, sent)] = harness.forwarded
    assert sent["assistant_id"] == graph
    # Nothing the remote graph is handed carries the configurable but the signed token.
    assert "config" not in sent
    assert sent["langsmith_tracer"] == {"project_name": "open-swe-test"}
    context = sent["context"]
    assert isinstance(context, dict) and context["__event_streaming_v2"] is True
    assert isinstance(token := context["__run_token"], str)
    signed = verify_runtime_token(token)
    assert signed.configurable == {"github_login": "ada", "repo": {"owner": "a", "name": "b"}}
    # The hooks see the run metadata an in-process run would, e.g. which delivered
    # task messages the transcript records.
    assert run_config(signed)["metadata"] == {
        "event_match_ids": ["match-1"],
        "ls_attribution_identifier": "trigger",
    }


@pytest.mark.parametrize("command", [False, True], ids=["rest", "command"])
@pytest.mark.parametrize(
    "permalink", ["https://langchain.slack.com/archives/C123/p1234567890000000", ""]
)
async def test_runs_include_the_current_slack_thread_link(
    monkeypatch: pytest.MonkeyPatch, secret: None, command: bool, permalink: str
) -> None:
    harness = _Harness(monkeypatch)
    await harness.request("POST", "/threads", {"thread_id": THREAD})
    await harness.request(
        "PATCH",
        f"/threads/{THREAD}",
        {"metadata": {"source_context": {"slack_thread": {"permalink": permalink}}}},
    )
    params: dict[str, JsonValue] = {
        "assistant_id": "agent",
        "input": {},
        "metadata": {"kind": "follow_up"},
        "config": {"metadata": {"invocation_id": "turn-1"}},
    }
    if command:
        status, _ = await harness.request(
            "POST",
            f"/threads/{THREAD}/commands",
            {"id": 1, "method": "run.start", "params": params},
        )
    else:
        status, _ = await harness.request("POST", f"/threads/{THREAD}/runs", params)

    assert status == 200
    [(_, sent)] = harness.forwarded
    expected: dict[str, JsonValue] = {"slack_thread_url": permalink} if permalink else {}
    assert sent["metadata"] == {"kind": "follow_up", **expected}
    assert sent["config"] == {"metadata": {"invocation_id": "turn-1", **expected}}


async def test_deleting_a_thread_removes_every_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda")
    await harness.runtimes.local.create(THREAD, {})
    await harness.runtimes.mda.create(THREAD, {})

    status, _ = await harness.request("DELETE", f"/threads/{THREAD}")

    assert status == 204
    assert await index.get(THREAD) is None
    assert harness.runtimes.local.threads == {} and harness.runtimes.mda.threads == {}


@_GRAPHS
async def test_a_run_started_by_command_on_mda_settles_through_the_completion_webhook(
    monkeypatch: pytest.MonkeyPatch, secret: None, metadata: dict[str, JsonValue], graph: str
) -> None:
    from openswe import dispatch

    monkeypatch.setattr(
        dispatch, "COMPLETION_WEBHOOK_URL", "https://swe.test/webhooks/run-complete"
    )
    harness = _Harness(monkeypatch)
    await harness.request("POST", "/threads", {"thread_id": THREAD, "metadata": metadata})
    command = {"id": 1, "method": "run.start", "params": {"assistant_id": "agent", "input": {}}}

    await harness.request("POST", f"/threads/{THREAD}/commands", command)

    indexed = await index.get(THREAD)
    assert indexed is not None and (indexed.runtime, indexed.status) == ("mda", "busy")
    [(path, sent)] = harness.forwarded
    assert path == f"/threads/{THREAD}/runs"
    assert sent["assistant_id"] == graph
    assert sent["webhook"] == "https://swe.test/webhooks/run-complete"


@pytest.mark.parametrize(
    ("params", "resumable"),
    [({}, True), ({"stream_resumable": False}, False)],
    ids=["streaming-client", "transcript-reader"],
)
async def test_a_run_started_by_command_keeps_its_stream_unless_the_client_opts_out(
    monkeypatch: pytest.MonkeyPatch, secret: None, params: dict[str, JsonValue], resumable: bool
) -> None:
    harness = _Harness(monkeypatch)
    await harness.request("POST", "/threads", {"thread_id": THREAD})
    command = {"id": 1, "method": "run.start", "params": {"assistant_id": "agent", **params}}

    await harness.request("POST", f"/threads/{THREAD}/commands", command)

    [(_, sent)] = harness.forwarded
    assert sent["stream_resumable"] is resumable


async def test_a_copy_of_an_mda_thread_is_indexed_on_mda(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda", metadata={"title": "source"})
    await harness.runtimes.mda.create(THREAD, {"title": "source"})

    status, made = await harness.request("POST", f"/threads/{THREAD}/copy", {})

    assert status == 200 and isinstance(made, dict)
    copy = await index.get(str(made["thread_id"]))
    assert copy is not None and copy.runtime == "mda" and copy.metadata == {"title": "source"}
    assert copy.thread_id in harness.runtimes.local.threads


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
