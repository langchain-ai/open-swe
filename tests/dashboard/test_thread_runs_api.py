"""The runs API ``@langchain/react`` 2.0 calls on ``/dashboard/api``, from the browser to MDA."""

import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from fastapi import FastAPI
from pydantic import JsonValue

from openswe.dashboard.oauth import require_session
from openswe.dispatch import SERVER_STREAM, RunFormat
from openswe.threads import index, proxy, routes, runtimes
from openswe.threads.principals import Principal, require_principal
from openswe.threads.start_request import SentRun
from openswe.transcript.turns import OpenTurn
from tests.conftest import patch_thread_module

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "00000000-0000-4000-8000-000000000001"
ELSEWHERE = "00000000-0000-4000-8000-000000000002"
RUN = "00000000-0000-4000-8000-0000000000a1"
OTHER_THREADS_RUN = "00000000-0000-4000-8000-0000000000b2"
EVENTS = b"id: 1-0\nevent: values\ndata: {}\n\n"
# A thread anyone signed in may read and post to, and one only its owner may.
SHARED = {"source": "dashboard", "owner_login": "ada", "visibility": "public"}
PRIVATE = {**SHARED, "visibility": "private"}

type Answer = Callable[[httpx2.Request], httpx2.Response]


class _NoThreadsHere:
    """This deployment's Agent Server, which keeps none of these threads."""

    async def indexed(self, thread_id: str) -> None:
        return None


class _MdaCopies:
    """MDA's SDK client, which the backend makes threads on and copies their metadata to."""

    def __init__(self) -> None:
        self.created: list[object] = []
        self.threads = self

    async def create(self, **kwargs: object) -> None:
        self.created.append(kwargs.get("thread_id"))

    async def update(self, *args: object, **kwargs: object) -> None:
        return None


class Mda:
    """MDA as the routes reach it: every request, by the pool it came through."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.requests: list[tuple[str, httpx2.Request]] = []
        self.answers: dict[tuple[str, str], Answer] = {
            ("GET", "/info"): lambda _: httpx2.Response(
                200, json={"flags": {"stream_lifecycle": True, "stream_namespaces": True}}
            ),
            ("POST", f"/threads/{THREAD}/runs"): lambda _: httpx2.Response(
                200, json={"run_id": RUN, "thread_id": THREAD, "status": "pending"}
            ),
            ("GET", f"/threads/{THREAD}/runs/{RUN}"): lambda _: httpx2.Response(
                200, json={"run_id": RUN, "thread_id": THREAD, "status": "running"}
            ),
            ("GET", f"/threads/{THREAD}/runs/{RUN}/stream"): lambda request: self.sse(
                request, f"/threads/{THREAD}/runs/{RUN}"
            ),
            ("GET", f"/threads/{THREAD}/stream"): lambda request: self.sse(request, None),
            ("GET", f"/threads/{THREAD}/state"): lambda _: httpx2.Response(
                200, json={"values": {"messages": []}, "next": []}
            ),
        }
        runtime = runtimes.MdaRuntime("http://mda.test")
        for pool in ("http", "read_http", "stream_http"):
            monkeypatch.setattr(runtimes.MdaRuntime, pool, self.client(pool))
        monkeypatch.setattr(runtimes.Runtimes, "mda_runtime", property(lambda _: runtime))
        monkeypatch.setattr(runtimes.Runtimes, "local", property(lambda _: _NoThreadsHere()))
        self.copies = _MdaCopies()
        monkeypatch.setattr(runtimes.MdaRuntime, "sdk", lambda _: self.copies)

        async def context(
            configurable: dict[str, JsonValue],
            *,
            thread_id: str,
            assistant_id: str,
            metadata: dict[str, JsonValue] | None = None,
        ) -> dict[str, JsonValue]:
            return {"graph": assistant_id, "__run_token": "signed"}

        monkeypatch.setattr(runtimes, "remote_run_context", context)

    def client(self, pool: str) -> property:
        def answer(request: httpx2.Request) -> httpx2.Response:
            self.requests.append((pool, request))
            reply = self.answers.get((request.method, request.url.path))
            return reply(request) if reply else httpx2.Response(404, json={"detail": "Not Found"})

        return property(
            lambda _: httpx2.AsyncClient(
                base_url="http://mda.test", transport=httpx2.MockTransport(answer)
            )
        )

    @staticmethod
    def sse(request: httpx2.Request, run_path: str | None) -> httpx2.Response:
        query = f"?{request.url.query.decode()}" if request.url.query else ""
        headers = {
            "content-type": "text/event-stream",
            # MDA's own paths, which name where it is mounted when it is.
            "location": f"http://mda.test/prefix{request.url.path}{query}",
        }
        if run_path is not None:
            headers["content-location"] = run_path
        return httpx2.Response(200, content=EVENTS, headers=headers)

    def sent(self, method: str, path: str) -> list[tuple[str, httpx2.Request]]:
        return [
            (pool, request)
            for pool, request in self.requests
            if (request.method, request.url.path) == (method, path)
        ]

    def started(self) -> dict[str, Any]:
        """The one run started on the thread, as MDA was asked for it."""
        [(_, request)] = self.sent("POST", f"/threads/{THREAD}/runs")
        return json.loads(request.content)


class Dashboard:
    """The thread routes as the browser calls them, signed in as ``login``."""

    def __init__(self) -> None:
        self.login: str | None = "ada"
        self.app = FastAPI()
        self.app.include_router(routes.router, prefix="/dashboard/api")
        self.app.dependency_overrides[require_session] = self.session
        self.app.dependency_overrides[require_principal] = self.principal

    async def session(self) -> dict[str, str]:
        assert self.login is not None
        return {"sub": self.login}

    async def principal(self) -> Principal:
        assert self.login is not None
        return Principal.of_login(self.login)

    def signed_out(self) -> None:
        self.login = None
        self.app.dependency_overrides.clear()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: JsonValue = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            return await client.request(method, f"/dashboard/api{path}", json=json, headers=headers)


@pytest.fixture
def mda(monkeypatch: pytest.MonkeyPatch) -> Mda:
    return Mda(monkeypatch)


@pytest.fixture
def dashboard() -> Dashboard:
    return Dashboard()


@pytest.fixture
def people(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a person's run is built from that lives outside the backend: their saved
    profile, their GitHub token and their email."""
    patch_thread_module(monkeypatch, "get_profile", AsyncMock(return_value={}))
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", AsyncMock())
    patch_thread_module(
        monkeypatch, "resolve_run_email", AsyncMock(return_value="person@example.com")
    )
    monkeypatch.setattr(
        "openswe.dashboard.profiles.get_valid_access_token", AsyncMock(return_value="token")
    )


async def _thread(
    metadata: Mapping[str, JsonValue], *, thread_id: str = THREAD, status: str = "idle"
) -> None:
    await index.create(
        thread_id, runtime="mda", metadata=dict(metadata), status=index.as_status(status) or "idle"
    )


async def test_info_is_mdas_not_this_deployments(mda: Mda, dashboard: Dashboard) -> None:
    response = await dashboard.request("GET", "/info")

    assert response.status_code == 200
    assert response.json() == {"flags": {"stream_lifecycle": True, "stream_namespaces": True}}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/info"),
        ("POST", f"/threads/{THREAD}/runs/stream"),
        ("GET", f"/threads/{THREAD}/runs/{RUN}/stream"),
        ("GET", f"/threads/{THREAD}/runs/{RUN}"),
        ("GET", f"/threads/{THREAD}/stream"),
    ],
)
async def test_a_signed_out_caller_reaches_nothing(
    mda: Mda, dashboard: Dashboard, method: str, path: str
) -> None:
    await _thread(SHARED)
    dashboard.signed_out()

    response = await dashboard.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == 401
    assert mda.requests == []


@pytest.mark.parametrize(
    "path",
    [
        f"/threads/{THREAD}/runs/{RUN}/stream",
        f"/threads/{THREAD}/runs/{RUN}",
        f"/threads/{THREAD}/runs",
        f"/threads/{THREAD}/stream",
        f"/threads/{ELSEWHERE}/stream",
    ],
)
async def test_whoever_cant_read_a_thread_cant_tell_it_exists(
    mda: Mda, dashboard: Dashboard, path: str
) -> None:
    await _thread(PRIVATE)
    dashboard.login = "bob"

    response = await dashboard.request("GET", path)

    assert response.status_code == 404
    assert mda.requests == []


@pytest.mark.parametrize(
    ("path", "query", "location", "content_location"),
    [
        pytest.param(
            f"/threads/{THREAD}/runs/{RUN}/stream",
            "stream_mode=values&namespace=tools%7Cabc&cancel_on_disconnect=0",
            f"/threads/{THREAD}/runs/{RUN}/stream"
            "?stream_mode=values&namespace=tools%7Cabc&cancel_on_disconnect=0",
            f"/threads/{THREAD}/runs/{RUN}",
            id="run-join",
        ),
        pytest.param(
            f"/threads/{THREAD}/stream",
            "stream_mode=lifecycle&stream_modes=lifecycle",
            f"/threads/{THREAD}/stream?stream_mode=lifecycle&stream_modes=lifecycle",
            None,
            id="thread-watcher",
        ),
    ],
)
async def test_a_reconnect_resumes_after_the_last_event_it_saw(
    mda: Mda,
    dashboard: Dashboard,
    path: str,
    query: str,
    location: str,
    content_location: str | None,
) -> None:
    await _thread(SHARED)
    dashboard.login = "bob"

    response = await dashboard.request(
        "GET", f"{path}?{query}", headers={"Last-Event-ID": "1724342400000-0"}
    )

    assert response.status_code == 200
    assert response.content == EVENTS
    assert response.headers["content-type"].startswith("text/event-stream")
    # Paths the SDK appends to its apiUrl, /dashboard/api, to reconnect and to read the run.
    assert response.headers["location"] == location
    assert response.headers.get("content-location") == content_location
    [(pool, joined)] = mda.sent("GET", path)
    assert pool == "stream_http"
    assert joined.url.query.decode() == query
    assert joined.headers["last-event-id"] == "1724342400000-0"


@pytest.mark.parametrize(
    "path",
    [
        f"/threads/{THREAD}/runs/{RUN}/stream",
        f"/threads/{THREAD}/runs/{RUN}",
        f"/threads/{THREAD}/stream",
    ],
)
async def test_a_read_never_makes_the_thread_on_mda_first(
    mda: Mda, dashboard: Dashboard, path: str
) -> None:
    await _thread(SHARED)

    response = await dashboard.request("GET", path)

    assert response.status_code == 200
    assert mda.copies.created == []


@pytest.mark.parametrize("query", ["cancel_on_disconnect=1", "", "cancel_on_disconnect=0"])
async def test_a_join_never_cancels_the_run_when_its_caller_leaves(
    mda: Mda, dashboard: Dashboard, query: str
) -> None:
    await _thread(SHARED)
    # Bob may read the thread, not stop its run.
    dashboard.login = "bob"

    response = await dashboard.request("GET", f"/threads/{THREAD}/runs/{RUN}/stream?{query}")

    assert response.status_code == 200
    [(_, joined)] = mda.sent("GET", f"/threads/{THREAD}/runs/{RUN}/stream")
    assert joined.url.params.get_list("cancel_on_disconnect") == ["0"]


async def test_a_run_is_read_and_rejoined_only_on_its_own_thread(
    mda: Mda, dashboard: Dashboard
) -> None:
    await _thread(SHARED)

    read = await dashboard.request("GET", f"/threads/{THREAD}/runs/{RUN}")
    other_read = await dashboard.request("GET", f"/threads/{THREAD}/runs/{OTHER_THREADS_RUN}")
    # MDA skips its own check that the run is the thread's for a join resuming after an event.
    other_join = await dashboard.request(
        "GET",
        f"/threads/{THREAD}/runs/{OTHER_THREADS_RUN}/stream",
        headers={"Last-Event-ID": "1724342400000-0"},
    )

    assert read.status_code == 200
    assert read.json()["run_id"] == RUN
    assert other_read.status_code == 404
    assert other_join.status_code == 404
    assert mda.sent("GET", f"/threads/{THREAD}/runs/{OTHER_THREADS_RUN}/stream") == []


@pytest.mark.parametrize("listed", [False, True], ids=["run", "runs"])
async def test_a_reader_never_gets_a_runs_token(
    mda: Mda, dashboard: Dashboard, listed: bool
) -> None:
    await _thread(SHARED)
    dashboard.login = "bob"
    path = f"/threads/{THREAD}/runs" if listed else f"/threads/{THREAD}/runs/{RUN}"
    run = {
        "run_id": RUN,
        "thread_id": THREAD,
        "status": "running",
        "kwargs": {
            "input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]},
            "stream_mode": ["values", "messages-tuple"],
            "subgraphs": True,
            "context": {"__run_token": "ada-run-token", "__catalog": {"tools": []}},
            "config": {
                "configurable": {"__run_token": "ada-run-token", "__event_streaming_v2": False}
            },
            "webhook": "https://backend.test/webhooks/run-complete?token=ada-run-token",
        },
    }
    mda.answers[("GET", path)] = lambda _: httpx2.Response(200, json=[run] if listed else run)

    response = await dashboard.request("GET", path)

    assert response.status_code == 200
    assert "ada-run-token" not in response.text
    seen = response.json()[0] if listed else response.json()
    assert seen["kwargs"] == {
        "input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]},
        "stream_mode": ["values", "messages-tuple"],
        "subgraphs": True,
        "config": {"configurable": {"__event_streaming_v2": False}},
    }


async def test_a_reader_joining_a_run_never_gets_its_token(mda: Mda, dashboard: Dashboard) -> None:
    await _thread(SHARED)
    dashboard.login = "bob"
    configurable = {"__run_token": "ada-run-token", "thread_id": THREAD}
    ids = {"thread_id": THREAD, "checkpoint_ns": "", "checkpoint_id": "c-2"}
    checkpoint = {
        "config": {"configurable": configurable},
        "parent_config": {"configurable": configurable},
        "checkpoint": ids,
        "values": {"messages": []},
    }
    values = b'id: 1-0\r\nevent: values\r\ndata: {"messages": []}\r\n\r\n'
    sent = (
        values
        + b"id: 2-0\r\nevent: checkpoints\r\ndata: "
        + json.dumps(checkpoint).encode()
        + b"\r\n\r\nid: 3-0\r\nevent: debug|tools:abc\r\ndata: "
        + json.dumps({"type": "checkpoint", "payload": checkpoint}).encode()
        + b"\r\n\r\n"
    )

    async def in_pieces() -> AsyncIterator[bytes]:
        for start in range(0, len(sent), 7):
            yield sent[start : start + 7]

    mda.answers[("GET", f"/threads/{THREAD}/runs/{RUN}/stream")] = lambda _: httpx2.Response(
        200, content=in_pieces(), headers={"content-type": "text/event-stream"}
    )

    response = await dashboard.request("GET", f"/threads/{THREAD}/runs/{RUN}/stream")

    assert response.status_code == 200
    assert "ada-run-token" not in response.text
    assert response.content.startswith(values)
    events = [event for event in response.text.split("\r\n\r\n") if event]
    assert [event.split("\r\n")[0] for event in events] == ["id: 1-0", "id: 2-0", "id: 3-0"]
    read = {"checkpoint": ids, "values": {"messages": []}}
    assert [json.loads(event.split("data: ", 1)[1]) for event in events] == [
        {"messages": []},
        read,
        {"type": "checkpoint", "payload": read},
    ]


async def test_a_message_steered_into_someone_elses_run_never_gets_its_token(
    monkeypatch: pytest.MonkeyPatch, mda: Mda, dashboard: Dashboard
) -> None:
    await _thread({**SHARED, "transcript": "v2", "latest_run_id": RUN}, status="busy")
    mda.answers[("GET", f"/threads/{THREAD}/runs/{RUN}")] = lambda _: httpx2.Response(
        200,
        json={
            "run_id": RUN,
            "thread_id": THREAD,
            "status": "running",
            "kwargs": {"context": {"__run_token": "ada-run-token"}, "stream_mode": ["values"]},
        },
    )

    async def steer_target(thread_id: str) -> OpenTurn | None:
        return OpenTurn(turn_id=uuid.uuid7(), run_id=RUN)

    async def steer(*args: object, **kwargs: object) -> SentRun:
        return SentRun(run_id=RUN, steered=True)

    monkeypatch.setattr(proxy, "steer_target", steer_target)
    monkeypatch.setattr(proxy, "steer_running_thread", steer)
    dashboard.login = "bob"

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/runs",
        json={"input": {"messages": [{"type": "human", "content": "and"}]}},
    )

    assert response.status_code == 200
    assert response.headers.get("x-robin-steered") == "true"
    assert "ada-run-token" not in response.text
    assert response.json()["kwargs"] == {"stream_mode": ["values"]}


async def test_a_streamed_message_keeps_only_the_metadata_the_sdk_reads(
    mda: Mda, dashboard: Dashboard
) -> None:
    await _thread(SHARED)
    chunk = {"type": "AIMessageChunk", "id": "run--1", "content": "Hel"}
    run_metadata = {
        "ls_model_name": "a-model",
        "user_email": "ada@example.com",
        "langgraph_step": 3,
    }
    root = {"langgraph_checkpoint_ns": "model:1", "checkpoint_ns": "model:1"}
    subagent = {
        "langgraph_checkpoint_ns": "tools:call-1|model:2",
        "checkpoint_ns": "tools:call-1|model:2",
        "lc_subagent_tool_call_id": "call-1",
    }
    # A v3 run's frame: its own ``metadata`` is the model's, and no run metadata rides on it.
    v3_frame = {"event": "message-start", "id": "run--2", "metadata": {"model_name": "a-model"}}
    sent = [
        ("messages", [chunk, {**root, **run_metadata}]),
        ("messages|tools:call-1", [chunk, {**subagent, **run_metadata}]),
        ("messages", v3_frame),
        ("custom", run_metadata),
    ]
    stream = b"".join(
        f"id: {at}-0\r\nevent: {name}\r\ndata: {json.dumps(data)}\r\n\r\n".encode()
        for at, (name, data) in enumerate(sent)
    )

    async def in_pieces() -> AsyncIterator[bytes]:
        for start in range(0, len(stream), 5):
            yield stream[start : start + 5]

    mda.answers[("GET", f"/threads/{THREAD}/runs/{RUN}/stream")] = lambda _: httpx2.Response(
        200, content=in_pieces(), headers={"content-type": "text/event-stream"}
    )

    response = await dashboard.request("GET", f"/threads/{THREAD}/runs/{RUN}/stream")

    events = [event for event in response.text.split("\r\n\r\n") if event]
    assert [event.split("\r\n")[1] for event in events] == [f"event: {name}" for name, _ in sent]
    assert [json.loads(event.split("data: ", 1)[1]) for event in events] == [
        [chunk, root],
        [chunk, subagent],
        v3_frame,
        run_metadata,
    ]


async def test_the_runtimes_refusal_is_the_streams_status(mda: Mda, dashboard: Dashboard) -> None:
    await _thread(SHARED)
    mda.answers[("GET", f"/threads/{THREAD}/stream")] = lambda _: httpx2.Response(
        422, json={"detail": "Invalid stream mode: everything"}
    )

    response = await dashboard.request("GET", f"/threads/{THREAD}/stream?stream_mode=everything")

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid stream mode: everything"}


async def test_a_message_starts_an_old_format_run_and_streams_it_from_its_first_event(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    await _thread(SHARED)
    run = {
        "input": {"messages": [{"type": "human", "content": "Fix the flaky test", "id": "m-1"}]},
        "metadata": {"owner_login": "mallory", "visibility": "public", "user_id": "mallory"},
        "stream_mode": ["values", "messages-tuple", "lifecycle"],
        "stream_subgraphs": True,
        "stream_resumable": False,
        "on_disconnect": "cancel",
        "durability": "exit",
        # Only the backend says where a run reports, what it runs and what it may do.
        "assistant_id": "elsewhere",
        "webhook": "https://attacker.test/hook",
        "context": {"__run_token": "forged", "__catalog": {}},
        "interrupt_before": ["tools"],
        "interrupt_after": ["model"],
        "after_seconds": 3600,
        "if_not_exists": "reject",
        "config": {"configurable": {"__event_streaming_v2": True}, "tags": ["forged"]},
    }

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/runs/stream?namespace=",
        json=run,
        headers={"x-robin-submitted-at": "1"},
    )

    assert response.status_code == 200
    assert response.content == EVENTS
    assert response.headers["location"] == f"/threads/{THREAD}/runs/{RUN}/stream?namespace="
    assert response.headers["content-location"] == f"/threads/{THREAD}/runs/{RUN}"
    assert "x-robin-steered" not in response.headers
    started = mda.started()
    assert started["context"]["__event_streaming_v2"] is False
    assert started["context"]["__run_token"] == "signed"
    assert "__catalog" not in started["context"]
    assert started["assistant_id"] == "open-swe"
    assert started.get("webhook") != "https://attacker.test/hook"
    assert started["if_not_exists"] == "create"
    for dropped in ("interrupt_before", "interrupt_after", "after_seconds", "on_disconnect"):
        assert dropped not in started
    # 2.0 reads these off the run to decide what it filters.
    assert started["stream_mode"] == ["values", "messages-tuple", "lifecycle"]
    assert started["stream_subgraphs"] is True
    # Joined after it starts, so it keeps its events for the join whatever the client asked.
    assert started["stream_resumable"] is True
    assert started["durability"] == "exit"
    assert "tags" not in started.get("config", {})
    # Attributed to the person who sent it, timed from when the backend received it.
    assert started["metadata"]["origin"] == "dashboard"
    assert "owner_login" not in started["metadata"]
    assert started["metadata"].get("user_id") != "mallory"
    assert started["metadata"]["dashboard_client"] == "other"
    assert isinstance(started["metadata"]["dashboard_ttft_started_at_ms"], int)
    [message] = started["input"]["messages"]
    assert "Fix the flaky test" in json.dumps(message)
    [(pool, joined)] = mda.sent("GET", f"/threads/{THREAD}/runs/{RUN}/stream")
    assert pool == "stream_http"
    assert joined.headers["last-event-id"] == "-"
    assert joined.url.query.decode() == "namespace=&cancel_on_disconnect=1"
    thread = await index.get(THREAD)
    assert thread is not None and thread.metadata["latest_run_id"] == RUN


@pytest.mark.parametrize(
    ("strategy", "open_turn", "handled", "streamed"),
    [
        pytest.param("interrupt", True, "steered", RUN, id="steers-into-the-open-turn"),
        pytest.param("enqueue", True, "queued", "queued-run", id="queues-when-asked"),
        pytest.param(None, False, "queued", "queued-run", id="queues-behind-an-ending-run"),
    ],
)
async def test_a_message_on_a_busy_thread_streams_the_run_it_joined_or_waits_for(
    monkeypatch: pytest.MonkeyPatch,
    mda: Mda,
    dashboard: Dashboard,
    strategy: str | None,
    open_turn: bool,
    handled: str,
    streamed: str,
) -> None:
    """As ``/commands`` does: busy and not ``enqueue`` steers into the open turn."""
    await _thread({**SHARED, "transcript": "v2", "latest_run_id": RUN}, status="busy")
    mda.answers[("GET", f"/threads/{THREAD}/runs/queued-run")] = lambda _: httpx2.Response(
        200, json={"run_id": "queued-run", "thread_id": THREAD, "status": "pending"}
    )
    mda.answers[("GET", f"/threads/{THREAD}/runs/queued-run/stream")] = lambda request: mda.sse(
        request, f"/threads/{THREAD}/runs/queued-run"
    )
    follow_ups: list[tuple[str, object]] = []

    async def steer_target(thread_id: str) -> OpenTurn | None:
        return OpenTurn(turn_id=uuid.uuid7(), run_id=RUN) if open_turn else None

    async def steer(*args: object, run_format: object, **kwargs: object) -> SentRun:
        follow_ups.append(("steered", run_format))
        return SentRun(run_id=RUN, steered=True)

    async def queue(*args: object, run_format: object, **kwargs: object) -> SentRun:
        follow_ups.append(("queued", run_format))
        return SentRun(run_id="queued-run", queued=True)

    monkeypatch.setattr(proxy, "steer_target", steer_target)
    monkeypatch.setattr(proxy, "steer_running_thread", steer)
    monkeypatch.setattr(proxy, "queue_follow_up_run", queue)
    run: dict[str, JsonValue] = {
        "input": {"messages": [{"type": "human", "content": "and this"}]},
        "stream_mode": ["values"],
        "on_disconnect": "cancel",
    }
    if strategy is not None:
        run["multitask_strategy"] = strategy

    response = await dashboard.request("POST", f"/threads/{THREAD}/runs/stream", json=run)

    assert response.status_code == 200
    assert response.headers["content-location"] == f"/threads/{THREAD}/runs/{streamed}"
    assert response.headers.get("x-robin-steered") == ("true" if handled == "steered" else None)
    [(how, run_format)] = follow_ups
    assert how == handled
    # A run started for the message streams as its client asked.
    assert run_format == RunFormat.of({"stream_mode": ["values"], "stream_resumable": True})
    assert mda.sent("POST", f"/threads/{THREAD}/runs") == []
    [(_, joined)] = mda.sent("GET", f"/threads/{THREAD}/runs/{streamed}/stream")
    # Leaving stops the message's own run, never the one it steered into.
    cancels = "0" if handled == "steered" else "1"
    assert joined.url.query.decode() == f"cancel_on_disconnect={cancels}"


async def test_an_answer_resumes_the_interrupted_run_in_the_old_format(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    await _thread({**SHARED, "latest_run_status": "interrupted"}, status="interrupted")
    answer = {"resume": {"interrupt-1": {"type": "approve"}}}

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/runs/stream",
        json={
            "command": {**answer, "unknown": True},
            "multitask_strategy": "rollback",
        },
    )

    assert response.status_code == 200
    started = mda.started()
    assert started["command"] == answer
    assert started["input"] is None
    # As input.respond on /commands: an answer can't roll back or interrupt a run.
    assert started["multitask_strategy"] == "reject"
    assert started["context"]["__event_streaming_v2"] is False
    assert started["stream_resumable"] is True
    assert len(mda.sent("GET", f"/threads/{THREAD}/runs/{RUN}/stream")) == 1


# The two ways 2.0 starts a run: streamed, and created to wait in the server's queue.
RUN_ROUTES = pytest.mark.parametrize("route", ["/runs/stream", "/runs"])


@RUN_ROUTES
@pytest.mark.parametrize(
    ("metadata", "status"),
    [
        pytest.param({**SHARED, "admin_thread": True}, 403, id="reader-who-may-not-post"),
        pytest.param(PRIVATE, 404, id="not-a-reader"),
    ],
)
async def test_only_who_may_post_to_a_thread_starts_a_run_there(
    mda: Mda, dashboard: Dashboard, route: str, metadata: dict[str, JsonValue], status: int
) -> None:
    await _thread(metadata)
    dashboard.login = "bob"

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}{route}",
        json={"input": {"messages": [{"type": "human", "content": "hi"}]}},
    )

    assert response.status_code == status
    assert mda.requests == []


async def test_a_reader_who_may_not_post_sends_no_command(mda: Mda, dashboard: Dashboard) -> None:
    await _thread({**SHARED, "admin_thread": True})
    dashboard.login = "bob"

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/commands",
        json={"id": 1, "method": "input.inject", "params": {}},
    )

    assert response.status_code == 403
    assert mda.requests == []


@RUN_ROUTES
async def test_a_run_on_a_missing_thread_creates_nothing(
    mda: Mda, dashboard: Dashboard, route: str
) -> None:
    response = await dashboard.request(
        "POST",
        f"/threads/{ELSEWHERE}{route}",
        json={"input": {"messages": [{"type": "human", "content": "hi"}]}},
    )

    assert response.status_code == 404
    assert await index.get(ELSEWHERE) is None
    assert mda.requests == []


@RUN_ROUTES
async def test_the_runtimes_refusal_of_a_run_is_its_status(
    mda: Mda, dashboard: Dashboard, people: None, route: str
) -> None:
    """2.0 retries a run MDA refuses with 422 without the stream modes it may not have."""
    await _thread(SHARED)
    mda.answers[("POST", f"/threads/{THREAD}/runs")] = lambda _: httpx2.Response(
        422, json={"detail": "Invalid stream mode: lifecycle"}
    )

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}{route}",
        json={
            "input": {"messages": [{"type": "human", "content": "hi"}]},
            "stream_mode": ["lifecycle"],
        },
    )

    assert response.status_code == 422
    assert "Invalid stream mode" in response.json()["detail"]


@pytest.mark.parametrize(
    ("caller", "status"),
    [pytest.param("ada", 200, id="reader"), pytest.param("bob", 404, id="not")],
)
async def test_the_sdks_create_answers_only_a_reader_for_a_thread_that_exists(
    mda: Mda, dashboard: Dashboard, caller: str, status: int
) -> None:
    await _thread(PRIVATE)
    dashboard.login = caller

    response = await dashboard.request(
        "POST",
        "/threads",
        json={"thread_id": THREAD, "metadata": {"graph_id": "agent"}, "if_exists": "do_nothing"},
    )

    assert response.status_code == status
    if status == 200:
        assert response.json()["thread_id"] == THREAD
        assert response.json()["metadata"] == PRIVATE


async def test_the_sdks_create_never_makes_a_thread(mda: Mda, dashboard: Dashboard) -> None:
    """A thread starts with its first message, which carries the composer's choices."""
    response = await dashboard.request(
        "POST",
        "/threads",
        json={
            "thread_id": ELSEWHERE,
            "metadata": {"owner_login": "ada"},
            "if_exists": "do_nothing",
        },
    )

    assert response.status_code == 404
    assert await index.get(ELSEWHERE) is None
    assert mda.requests == []


async def test_a_prompt_still_starts_a_session(
    monkeypatch: pytest.MonkeyPatch, dashboard: Dashboard
) -> None:
    create = AsyncMock(return_value="new-thread")
    monkeypatch.setattr(routes, "create_dashboard_session", create)

    response = await dashboard.request(
        "POST", "/threads", json={"prompt": "Fix login", "start": False, "thread_id": THREAD}
    )
    # Invalid as a session, it is not taken for the SDK's create instead.
    invalid = await dashboard.request("POST", "/threads", json={"prompt": "", "thread_id": THREAD})

    assert response.status_code == 201
    assert response.json() == {"thread_id": "new-thread"}
    assert invalid.status_code == 422
    assert await index.get(THREAD) is None


async def test_a_command_starts_an_old_format_run_whatever_stream_its_client_asks_for(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    """A thread view joins any run in the modes it started with, so the server picks them."""
    await _thread(SHARED)
    command = {
        "id": 1,
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]},
            "config": {"configurable": {"__event_streaming_v2": True}},
            "assistant_id": "agent",
            "stream_mode": ["values"],
            "stream_resumable": False,
        },
    }

    response = await dashboard.request("POST", f"/threads/{THREAD}/commands", json=command)

    assert response.status_code == 200
    assert response.json() == {"id": 1, "type": "success", "result": {"run_id": RUN}}
    started = mda.started()
    assert started["context"]["__event_streaming_v2"] is False
    stream = SERVER_STREAM.run_fields()
    assert {key: started[key] for key in stream} == stream


async def test_a_command_the_runtime_refuses_keeps_its_reply_shape(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    await _thread(SHARED)
    mda.answers[("POST", f"/threads/{THREAD}/runs")] = lambda _: httpx2.Response(
        422, json={"detail": "Invalid stream mode: lifecycle"}
    )
    command = {
        "id": 4,
        "method": "run.start",
        "params": {"input": {"messages": [{"type": "human", "content": "hi"}]}},
    }

    response = await dashboard.request("POST", f"/threads/{THREAD}/commands", json=command)

    assert response.status_code == 200
    reply = response.json()
    assert reply == {
        "type": "error",
        "id": 4,
        "error": "unknown_error",
        "message": reply["message"],
    }
    assert "Invalid stream mode" in reply["message"]


async def test_an_answer_by_command_resumes_the_run_through_the_runs_api(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    await _thread({**SHARED, "latest_run_status": "interrupted"}, status="interrupted")
    command = {
        "id": 3,
        "method": "input.respond",
        "params": {"interrupt_id": "interrupt-1", "response": {"type": "approve"}},
    }

    response = await dashboard.request("POST", f"/threads/{THREAD}/commands", json=command)

    assert response.status_code == 200
    assert response.json() == {"id": 3, "type": "success", "result": {}}
    started = mda.started()
    assert started["command"] == {"resume": {"interrupt-1": {"type": "approve"}}}
    assert started["input"] is None
    assert started["context"]["__event_streaming_v2"] is False
    assert started["stream_resumable"] is True
    assert mda.sent("POST", f"/threads/{THREAD}/commands") == []


async def test_a_prompt_starts_its_session_with_an_old_format_run(
    monkeypatch: pytest.MonkeyPatch, mda: Mda, dashboard: Dashboard, people: None
) -> None:
    async def create(*args: object, **kwargs: object) -> str:
        await _thread(SHARED)
        return THREAD

    monkeypatch.setattr(routes, "create_dashboard_session", create)

    response = await dashboard.request("POST", "/threads", json={"prompt": "Fix login"})

    assert response.status_code == 201
    assert mda.started()["context"]["__event_streaming_v2"] is False


async def test_a_run_created_on_an_idle_thread_starts_old_format_and_answers_unstreamed(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    await _thread(SHARED)

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/runs",
        json={
            "input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]},
            "stream_mode": ["values", "messages-tuple"],
            "stream_resumable": False,
            "multitask_strategy": "rollback",
        },
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == RUN
    # A run begun since the busy check is never stopped: the start is refused, then queued.
    assert mda.started()["multitask_strategy"] == "reject"
    assert "x-robin-steered" not in response.headers
    started = mda.started()
    assert started["context"]["__event_streaming_v2"] is False
    assert started["stream_mode"] == ["values", "messages-tuple"]
    # Joined later, from its first event.
    assert started["stream_resumable"] is True
    assert mda.sent("GET", f"/threads/{THREAD}/runs/{RUN}/stream") == []


async def test_a_run_created_without_stream_fields_streams_as_the_servers_runs_do(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    await _thread(SHARED)

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/runs",
        json={"input": {"messages": [{"type": "human", "content": "Fix the flaky test"}]}},
    )

    assert response.status_code == 200
    started = mda.started()
    assert {key: started.get(key) for key in SERVER_STREAM.fields} == SERVER_STREAM.run_fields()


@pytest.mark.parametrize(
    ("strategy", "handled", "answered"),
    [
        pytest.param("enqueue", "queued", "queued-run", id="queues-on-enqueue"),
        pytest.param(None, "steered", RUN, id="steers-otherwise"),
    ],
)
async def test_a_run_created_on_a_busy_thread_queues_or_steers_as_commands_do(
    monkeypatch: pytest.MonkeyPatch,
    mda: Mda,
    dashboard: Dashboard,
    strategy: str | None,
    handled: str,
    answered: str,
) -> None:
    await _thread({**SHARED, "transcript": "v2", "latest_run_id": RUN}, status="busy")
    mda.answers[("GET", f"/threads/{THREAD}/runs/queued-run")] = lambda _: httpx2.Response(
        200, json={"run_id": "queued-run", "thread_id": THREAD, "status": "pending"}
    )
    follow_ups: list[str] = []

    async def steer_target(thread_id: str) -> OpenTurn | None:
        return OpenTurn(turn_id=uuid.uuid7(), run_id=RUN)

    async def steer(*args: object, **kwargs: object) -> SentRun:
        follow_ups.append("steered")
        return SentRun(run_id=RUN, steered=True)

    async def queue(*args: object, **kwargs: object) -> SentRun:
        follow_ups.append("queued")
        return SentRun(run_id="queued-run", queued=True)

    monkeypatch.setattr(proxy, "steer_target", steer_target)
    monkeypatch.setattr(proxy, "steer_running_thread", steer)
    monkeypatch.setattr(proxy, "queue_follow_up_run", queue)
    run: dict[str, JsonValue] = {"input": {"messages": [{"type": "human", "content": "and"}]}}
    if strategy is not None:
        run["multitask_strategy"] = strategy

    response = await dashboard.request("POST", f"/threads/{THREAD}/runs", json=run)

    assert response.status_code == 200
    assert follow_ups == [handled]
    assert response.json()["run_id"] == answered
    assert response.headers.get("x-robin-steered") == ("true" if handled == "steered" else None)
    assert mda.sent("POST", f"/threads/{THREAD}/runs") == []


async def test_a_run_created_with_a_command_answers_the_interrupt(
    mda: Mda, dashboard: Dashboard, people: None
) -> None:
    await _thread({**SHARED, "latest_run_status": "interrupted"}, status="interrupted")

    response = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/runs",
        json={"command": {"resume": {"interrupt-1": "yes"}}, "multitask_strategy": "enqueue"},
    )

    assert response.status_code == 200
    started = mda.started()
    assert started["command"] == {"resume": {"interrupt-1": "yes"}}
    assert started["multitask_strategy"] == "reject"
    assert started["context"]["__event_streaming_v2"] is False


@pytest.mark.parametrize(
    ("command", "status", "reply"),
    [
        pytest.param(
            {"id": 5, "method": "run.start", "params": {"input": {}}},
            200,
            {"type": "error", "id": 5, "error": "invalid_argument"},
            id="start",
        ),
        pytest.param(
            {"id": 5, "method": "input.respond", "params": {"interrupt_id": "i-1"}},
            409,
            {},
            id="answer",
        ),
    ],
)
async def test_a_command_on_a_channel_thread_it_cant_act_on_is_refused_as_before(
    monkeypatch: pytest.MonkeyPatch,
    mda: Mda,
    dashboard: Dashboard,
    people: None,
    command: dict[str, JsonValue],
    status: int,
    reply: dict[str, JsonValue],
) -> None:
    """A start's refusal is protocol v2's error, and an answer's an HTTP status."""
    monkeypatch.delenv("MDA_SERVICE_SECRET", raising=False)
    await _thread({**SHARED, "channel": "linear", "latest_run_status": "interrupted"})

    response = await dashboard.request("POST", f"/threads/{THREAD}/commands", json=command)

    assert response.status_code == status
    assert response.json().items() >= reply.items()
    assert "linear channel" in str(response.json())
    assert mda.sent("POST", f"/threads/{THREAD}/runs") == []


async def test_join_skips_run_preflight_only_for_server_recorded_latest_run(
    mda: Mda, dashboard: Dashboard
) -> None:
    await _thread({**SHARED, "latest_run_id": RUN})
    dashboard.login = "bob"

    joined = await dashboard.request(
        "GET",
        f"/threads/{THREAD}/runs/{RUN}/stream",
        headers={"Last-Event-ID": "1724342400000-0"},
    )
    other = await dashboard.request(
        "GET",
        f"/threads/{THREAD}/runs/{OTHER_THREADS_RUN}/stream",
        headers={"Last-Event-ID": "1724342400000-0"},
    )

    assert joined.status_code == 200
    assert joined.content == EVENTS
    assert mda.sent("GET", f"/threads/{THREAD}/runs/{RUN}") == []
    assert len(mda.sent("GET", f"/threads/{THREAD}/runs/{RUN}/stream")) == 1
    assert other.status_code == 404
    assert len(mda.sent("GET", f"/threads/{THREAD}/runs/{OTHER_THREADS_RUN}")) == 1
    assert mda.sent("GET", f"/threads/{THREAD}/runs/{OTHER_THREADS_RUN}/stream") == []


async def test_user_routes_cannot_forge_latest_run_id(
    monkeypatch: pytest.MonkeyPatch, mda: Mda, dashboard: Dashboard, people: None
) -> None:
    from types import SimpleNamespace

    from openswe.threads import handlers

    await _thread(SHARED)
    monkeypatch.setattr(handlers, "langgraph_client", lambda: SimpleNamespace(threads=mda.copies))
    monkeypatch.setattr(handlers, "mirror_thread_metadata", AsyncMock())
    monkeypatch.setattr(
        handlers,
        "_authorized_thread",
        AsyncMock(return_value={"thread_id": THREAD, "metadata": dict(SHARED)}),
    )

    renamed = await dashboard.request(
        "PATCH",
        f"/threads/{THREAD}",
        json={"title": "Renamed", "metadata": {"latest_run_id": OTHER_THREADS_RUN}},
    )
    assert renamed.status_code == 200
    saved = await index.get(THREAD)
    assert saved is not None and saved.metadata.get("latest_run_id") is None

    started = await dashboard.request(
        "POST",
        f"/threads/{THREAD}/runs",
        json={
            "input": {"messages": [{"type": "human", "content": "hi"}]},
            "metadata": {"latest_run_id": OTHER_THREADS_RUN},
        },
    )
    assert started.status_code == 200
    saved = await index.get(THREAD)
    assert saved is not None and saved.metadata.get("latest_run_id") == RUN


async def test_info_caches_successful_reads_for_five_minutes(
    monkeypatch: pytest.MonkeyPatch, mda: Mda, dashboard: Dashboard
) -> None:
    clock = 1000.0
    monkeypatch.setattr(runtimes.time, "monotonic", lambda: clock)

    mda.answers[("GET", "/info")] = lambda _: httpx2.Response(503, json={"detail": "busy"})
    failed = await dashboard.request("GET", "/info")
    assert failed.status_code == 503
    assert len(mda.sent("GET", "/info")) == 1

    mda.answers[("GET", "/info")] = lambda _: httpx2.Response(
        200, json={"flags": {"stream_lifecycle": True, "stream_namespaces": True}}
    )
    first = await dashboard.request("GET", "/info")
    second = await dashboard.request("GET", "/info")
    assert first.status_code == 200 and second.status_code == 200
    assert len(mda.sent("GET", "/info")) == 2

    clock += 299.0
    third = await dashboard.request("GET", "/info")
    assert third.status_code == 200
    assert len(mda.sent("GET", "/info")) == 2

    clock += 2.0
    fourth = await dashboard.request("GET", "/info")
    assert fourth.status_code == 200
    assert len(mda.sent("GET", "/info")) == 3
