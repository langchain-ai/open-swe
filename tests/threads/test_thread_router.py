import json
from collections.abc import Mapping
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import JsonValue
from starlette.types import Message, Receive, Scope, Send

from openswe.remote_runtime.runs import run_config
from openswe.remote_runtime.tokens import RemoteRun, sign_runtime_token, verify_runtime_token
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
        # Calls that reached MDA through its read client, as (method, path).
        self.read: list[tuple[str, str]] = []
        self.reached_here: list[str] = []
        self.local_status = 200

        def mda(request: httpx.Request) -> httpx.Response:
            self.forwarded.append((request.url.path, json.loads(request.content or b"{}")))
            return httpx.Response(200, json={"run_id": "remote-run"})

        mda_runtime = runtimes.MdaRuntime("http://mda.test")
        mda_runtime._http = httpx.AsyncClient(
            base_url=mda_runtime.url, transport=httpx.MockTransport(mda)
        )

        def mda_reads(request: httpx.Request) -> httpx.Response:
            self.read.append((request.method, request.url.path))
            return httpx.Response(200, json={})

        mda_runtime._read_http = httpx.AsyncClient(
            base_url=mda_runtime.url, transport=httpx.MockTransport(mda_reads)
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


@pytest.mark.parametrize("runtime", ["local", "mda"])
async def test_a_run_goes_to_its_threads_runtime_and_marks_the_thread_busy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    secret: None,
    runtime: Runtime,
) -> None:
    harness = _Harness(monkeypatch)
    # A local thread is one made before every new thread ran on MDA.
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
    warnings = [r for r in caplog.records if r.name == router.__name__ and r.levelname == "WARNING"]
    if runtime == "local":
        assert harness.reached_here == [f"/threads/{THREAD}/runs"]
        assert harness.forwarded == []
        [warning] = warnings
        assert warning.__dict__["thread_id"] == THREAD
        assert warning.__dict__["assistant_id"] == "agent"
    else:
        assert warnings == []
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


async def test_a_new_thread_runs_on_mda(monkeypatch: pytest.MonkeyPatch, secret: None) -> None:
    harness = _Harness(monkeypatch)
    await harness.request(
        "POST", "/threads", {"thread_id": THREAD, "metadata": {"owner_login": "ada"}}
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
        "metadata": {"kind": "task_message", "event_match_ids": ["match-1"]},
    }

    await harness.request("POST", f"/threads/{THREAD}/runs", run)

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"
    assert indexed.metadata["runtime"] == "mda"
    assert harness.runtimes.mda.threads[THREAD]["metadata"] == indexed.metadata
    assert harness.runtimes.local.threads[THREAD]["metadata"] == indexed.metadata
    [(_, sent)] = harness.forwarded
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
    assert run_config(signed)["metadata"] == {"event_match_ids": ["match-1"]}


async def test_a_channel_thread_is_indexed_for_mda_without_creating_it_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)

    await router.adopt_channel_run(THREAD, "linear")
    # The session's metadata is written after, as a create that finds the thread.
    await harness.request(
        "POST",
        "/threads",
        {"thread_id": THREAD, "metadata": {"source": "linear"}, "if_exists": "do_nothing"},
    )

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"
    assert indexed.status == "busy"
    assert indexed.metadata["channel"] == "linear"
    assert harness.runtimes.local.threads[THREAD]["metadata"]["runtime"] == "mda"
    # MDA's channel creates it as its conversation's own thread; a copy made
    # here first would belong to the backend and keep the channel out.
    assert THREAD not in harness.runtimes.mda.threads


async def _channel_thread(harness: _Harness) -> None:
    await router.adopt_channel_run(THREAD, "linear")
    await harness.request(
        "POST",
        "/threads",
        {"thread_id": THREAD, "metadata": {"source": "linear"}, "if_exists": "do_nothing"},
    )
    harness.runtimes.mda_runtime._mda_threads.discard(THREAD)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/runs", {"assistant_id": "agent", "input": {}}),
        ("POST", "/runs/stream", {"assistant_id": "agent", "input": {}}),
        ("POST", "/state", {"values": {}}),
        ("POST", "/copy", {}),
        ("POST", "/runs/run-1/cancel", None),
        ("POST", "/commands", {"id": 1, "method": "input.respond", "params": {}}),
    ],
)
async def test_a_channel_thread_refuses_writes(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: JsonValue
) -> None:
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, reply = await harness.request(method, f"/threads/{THREAD}{path}", body)

    # The channel starts its threads' runs; the backend can't, and says so.
    assert status == 409
    assert isinstance(reply, dict) and "linear channel" in str(reply["detail"])
    assert harness.forwarded == [] and harness.read == []
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"


async def test_a_channel_thread_refuses_a_run_started_by_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)
    command = {"id": 7, "method": "run.start", "params": {"assistant_id": "agent", "input": {}}}

    status, reply = await harness.request("POST", f"/threads/{THREAD}/commands", command)

    assert status == 200
    assert isinstance(reply, dict) and (reply["type"], reply["id"]) == ("error", 7)
    assert harness.forwarded == []


async def test_a_cancel_of_every_run_on_a_channel_thread_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, _ = await harness.request("POST", "/runs/cancel", {"thread_id": THREAD})

    assert status == 409
    assert harness.forwarded == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/state", None),
        ("GET", "/runs", None),
        ("GET", "/runs/run-1/join", None),
        ("POST", "/history", {"limit": 10}),
        ("POST", "/commands", {"id": 1, "method": "subscription.subscribe", "params": {}}),
    ],
)
async def test_reads_reach_mda_as_studio_for_channel_and_own_threads(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: JsonValue
) -> None:
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, _ = await harness.request(method, f"/threads/{THREAD}{path}", body)

    assert status == 200
    assert harness.read == [(method, f"/threads/{THREAD}{path}")]
    assert harness.forwarded == []
    # The channel created the thread on MDA; the backend creates no copy of its own.
    assert THREAD not in harness.runtimes.mda_runtime._mda_threads


async def test_a_channel_threads_metadata_is_copied_only_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, _ = await harness.request(
        "PATCH", f"/threads/{THREAD}", {"metadata": {"title": "Renamed"}}
    )

    assert status == 200
    assert harness.runtimes.local.threads[THREAD]["metadata"]["title"] == "Renamed"
    # On MDA the thread is the channel's; the backend writes nothing there.
    assert THREAD not in harness.runtimes.mda.threads


async def test_writes_to_the_backends_own_mda_threads_go_through_the_owner_scoped_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda")

    await harness.request("POST", f"/threads/{THREAD}/runs/run-1/cancel")
    await harness.request("GET", f"/threads/{THREAD}/state")

    assert [path for path, _ in harness.forwarded] == [f"/threads/{THREAD}/runs/run-1/cancel"]
    assert harness.read == [("GET", f"/threads/{THREAD}/state")]


async def test_deleting_a_thread_removes_every_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda")
    await harness.runtimes.local.create(THREAD, {})
    await harness.runtimes.mda.create(THREAD, {})

    status, _ = await harness.request("DELETE", f"/threads/{THREAD}")

    assert status == 204
    assert await index.get(THREAD) is None
    assert harness.runtimes.local.threads == {} and harness.runtimes.mda.threads == {}


async def test_a_run_started_by_command_on_mda_settles_through_the_completion_webhook(
    monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    from openswe import dispatch

    monkeypatch.setattr(
        dispatch, "COMPLETION_WEBHOOK_URL", "https://swe.test/webhooks/run-complete"
    )
    harness = _Harness(monkeypatch)
    await harness.request("POST", "/threads", {"thread_id": THREAD, "metadata": {}})
    command = {"id": 1, "method": "run.start", "params": {"assistant_id": "agent", "input": {}}}

    await harness.request("POST", f"/threads/{THREAD}/commands", command)

    indexed = await index.get(THREAD)
    assert indexed is not None and (indexed.runtime, indexed.status) == ("mda", "busy")
    [(path, sent)] = harness.forwarded
    assert path == f"/threads/{THREAD}/runs"
    assert sent["webhook"] == "https://swe.test/webhooks/run-complete"


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


_CHANNEL_CONVERSATION: list[dict[str, JsonValue]] = [
    {"type": "human", "id": "h1", "content": "Fix MYT-18"},
    {
        "type": "ai",
        "id": "a1",
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "open_pr", "args": {}}],
    },
    {"type": "tool", "id": "t1", "tool_call_id": "call-1", "content": "PR #7 opened"},
    {"type": "ai", "id": "a2", "content": "Opened PR #7."},
]


@pytest.mark.parametrize("channel", ["linear", "github"])
async def test_pr_feedback_on_a_channel_conversation_continues_on_the_pr_thread(
    monkeypatch: pytest.MonkeyPatch, secret: None, channel: str
) -> None:
    """A review on a PR a channel conversation opened runs on the PR's own thread,
    through the real router, starting with the conversation: the channel's thread
    refuses the backend's runs (409)."""
    from langgraph_sdk.client import LangGraphClient

    from openswe import dispatch
    from openswe.github import webhook
    from openswe.thread_ids import pr_comment_thread_id
    from openswe.users import User
    from openswe.webhooks import common

    harness = _Harness(monkeypatch)
    await router.adopt_channel_run(THREAD, channel)

    def reads(request: httpx.Request) -> httpx.Response:
        harness.read.append((request.method, request.url.path))
        if request.url.path == f"/threads/{THREAD}/state":
            return httpx.Response(200, json={"values": {"messages": _CHANNEL_CONVERSATION}})
        return httpx.Response(200, json={"values": {}})

    harness.runtimes.mda_runtime._read_http = httpx.AsyncClient(
        base_url=harness.runtimes.mda_runtime.url, transport=httpx.MockTransport(reads)
    )
    # MDA creates the PR's thread as the backend's own, through the recorded transport.
    mda = harness.runtimes.mda_runtime
    monkeypatch.setattr(mda, "sdk", lambda: LangGraphClient(mda._http))
    backend = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.router), base_url="http://backend"
    )
    monkeypatch.setattr(common, "get_client", lambda **kwargs: LangGraphClient(backend))
    monkeypatch.setattr(dispatch, "get_client", lambda **kwargs: LangGraphClient(backend))
    # GitHub itself stands in: the PR, its review, and who wrote it.
    monkeypatch.setattr(
        common,
        "extract_pr_context",
        AsyncMock(
            return_value=(
                {"owner": "o", "name": "r"},
                7,
                f"open-swe/{THREAD}",
                "ada",
                "https://github.com/o/r/pull/7",
                42,
                None,
            )
        ),
    )
    monkeypatch.setattr(User, "email_for_login", AsyncMock(return_value="ada@example.com"))
    monkeypatch.setattr(User, "known_logins", AsyncMock(return_value=frozenset({"ada"})))
    monkeypatch.setattr(common, "get_or_resolve_thread_github_token", AsyncMock(return_value="t"))
    review = {"body": "Rename the helper", "author": "ada", "type": "review"}
    monkeypatch.setattr(common, "fetch_pr_event_comments", AsyncMock(return_value=[review]))
    react = AsyncMock()
    monkeypatch.setattr(common, "react_to_github_comment", react)

    await webhook.process_github_pr_comment(
        {
            "action": "submitted",
            "sender": {"login": "ada", "id": 1},
            "review": {
                "body": "Rename the helper",
                "user": {"login": "ada"},
                "state": "changes_requested",
                "submitted_at": "2026-01-01T00:00:00Z",
            },
        },
        "pull_request_review",
        agent_thread_id=THREAD,
    )

    pr_thread = pr_comment_thread_id("o", "r", 7)
    runs = [body for path, body in harness.forwarded if path.endswith("/runs")]
    assert [path for path, _ in harness.forwarded if path.endswith("/runs")] == [
        f"/threads/{pr_thread}/runs"
    ]
    input = runs[0]["input"]
    assert isinstance(input, dict) and isinstance(messages := input["messages"], list)
    # The conversation first, then the review.
    assert messages[: len(_CHANNEL_CONVERSATION)] == _CHANNEL_CONVERSATION
    assert "Rename the helper" in json.dumps(messages[len(_CHANNEL_CONVERSATION) :])
    pr_metadata = (await index.get(pr_thread)).metadata  # type: ignore[union-attr]
    assert pr_metadata["continues_thread"] == THREAD
    # The channel's thread got no run, and the review its 👀 only once one started.
    assert all(f"/threads/{THREAD}/runs" not in path for path, _ in harness.forwarded)
    react.assert_awaited_once()
