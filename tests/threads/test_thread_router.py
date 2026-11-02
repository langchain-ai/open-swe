import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from managed_deepagents import CredentialStoreError
from managed_deepagents._agent_auth import AgentAuthContext, ResolvePrincipalIdentity
from pydantic import JsonValue
from starlette.types import Message, Receive, Scope, Send

from openswe.agent_api.connections import ACTING_USER_KEY
from openswe.agent_api.runs import run_config
from openswe.agent_api.tokens import RemoteRun, sign_runtime_token, verify_runtime_token
from openswe.threads import index, proxy, router, runtimes, store
from openswe.threads.index import Runtime, ThreadStatus
from openswe.threads.local_writes import LocalAgentServerWriteError
from openswe.threads.principals import Principal
from openswe.users.models import User

pytestmark = pytest.mark.usefixtures("registry_db")

THREAD = "00000000-0000-4000-8000-000000000001"
# The conversation MDA's Linear channel keeps the thread for: its owner there.
CHANNEL_OWNER = "mda:source-thread:linear:org/ISS-1"
SERVICE_SECRET = "whsec_a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="
# What the backend sends to act in that conversation: MDA's trusted-backend ingress.
AS_THE_CONVERSATION = {
    "x-mda-ingress-secret": SERVICE_SECRET,
    "x-mda-user-id": "mda:channel",
    "x-mda-source-provider": "linear",
    "x-mda-source-thread-id": "org/ISS-1",
}
# A person's Open SWE user, which ``resolve_user`` names their channel runs by.
USER_ID = "0190a3c4-0000-7000-8000-00000000ada0"
# MDA's workspace, and the Agent Auth principal its channels run that user as there.
WORKSPACE = "6f1e2d3c-0000-4000-8000-0000000000aa"
PRINCIPAL = "8c4b2a19-0000-4000-8000-0000000000bb"
AS_THE_PERSON = {
    **AS_THE_CONVERSATION,
    "x-mda-user-id": PRINCIPAL,
    "x-mda-agent-auth-principal-id": PRINCIPAL,
}


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

    def __init__(self, *, sealed: bool = False) -> None:
        self.threads: dict[str, dict[str, JsonValue]] = {}
        self.client = _Client(self)
        self.reachable = True
        # The local Agent Server, which refuses every create as the sealed one does.
        self.sealed = sealed

    async def get(self, thread_id: str) -> dict[str, JsonValue] | None:
        return self.threads.get(thread_id)

    async def indexed(self, thread_id: str) -> index.IndexedThread | None:
        if not self.reachable:
            raise httpx.ConnectError("Agent Server unreachable")
        thread = self.threads.get(thread_id)
        return None if thread is None else index.IndexedThread.from_agent_server(thread)

    async def create(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        if self.sealed:
            raise LocalAgentServerWriteError("Threads.put")
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
        self.local = _Threads(sealed=True)
        self.mda = _Threads()
        self.mda_runtime = mda_runtime

    def runs_on(self, runtime: Runtime) -> _Threads:
        return self.mda if runtime == "mda" else self.local


class _Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.forwarded: list[tuple[str, dict[str, JsonValue]]] = []
        # The conversation each forwarded write acted in, by its MDA headers.
        self.acted_as: list[dict[str, str]] = []
        # Calls that reached MDA through its read client, as (method, path).
        self.read: list[tuple[str, str]] = []
        self.reached_here: list[str] = []
        self.local_status = 200
        self.while_starting: Callable[[], Awaitable[object]] | None = None

        def mda(request: httpx.Request) -> httpx.Response:
            self.forwarded.append((request.url.path, json.loads(request.content or b"{}")))
            self.acted_as.append({k: v for k, v in request.headers.items() if k in AS_THE_PERSON})
            return httpx.Response(
                200,
                json={"run_id": "remote-run"},
                headers={"content-location": f"/threads/{THREAD}/runs/remote-run"},
            )

        mda_runtime = runtimes.MdaRuntime("http://mda.test")
        mda_runtime._http = httpx.AsyncClient(
            base_url=mda_runtime.url, transport=httpx.MockTransport(mda)
        )

        def mda_reads(request: httpx.Request) -> httpx.Response:
            self.read.append((request.method, request.url.path))
            if (request.method, request.url.path) == ("GET", f"/threads/{THREAD}"):
                return httpx.Response(200, json={"metadata": {"owner": CHANNEL_OWNER}})
            return httpx.Response(200, json={})

        mda_runtime._read_http = httpx.AsyncClient(
            base_url=mda_runtime.url, transport=httpx.MockTransport(mda_reads)
        )
        mda_runtime._mda_threads.add(THREAD)
        self.runtimes = _Runtimes(mda_runtime)
        monkeypatch.setattr(router, "RUNTIMES", self.runtimes)
        monkeypatch.setattr(store, "RUNTIMES", self.runtimes)

        async def context(
            configurable: dict[str, JsonValue],
            *,
            thread_id: str,
            assistant_id: str,
            metadata: dict[str, JsonValue] | None = None,
        ) -> dict[str, JsonValue]:
            if self.while_starting is not None:
                await self.while_starting()
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
    await store.THREADS.copies.settled(THREAD)
    assert harness.runtimes.local.threads == {}
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
    # A stop before this run asked to start leaves it be.
    stopped = {index.STOPPED_AT_KEY: runtimes.now_ms() - 1000}
    await index.create(THREAD, runtime="mda", metadata={"owner_login": "ada", **stopped})
    run = {
        "assistant_id": "agent",
        "input": {},
        "config": {"configurable": {"github_login": "ada"}},
        "if_not_exists": "reject",
    }

    status, _ = await harness.request("POST", f"/threads/{THREAD}/runs", run)

    assert status == 200
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.status == "busy"
    assert harness.reached_here == []
    [(path, sent)] = harness.forwarded
    assert path == f"/threads/{THREAD}/runs" and sent["assistant_id"] == "open-swe"
    # The index has the thread, so MDA makes its copy if it swept or never got one.
    assert sent["if_not_exists"] == "create"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/runs", {"assistant_id": "agent"}),
        ("/commands", {"id": 1, "method": "run.start", "params": {"assistant_id": "agent"}}),
    ],
)
async def test_a_stop_while_a_run_starts_interrupts_it_once_mda_has_it(
    monkeypatch: pytest.MonkeyPatch, secret: None, path: str, body: dict[str, JsonValue]
) -> None:
    # Working out the run takes a while, and a stop meanwhile finds no run to cancel.
    harness = _Harness(monkeypatch)
    await index.create(THREAD, runtime="mda", metadata={"owner_login": "ada"})
    harness.while_starting = lambda: index.update(THREAD, {index.STOPPED_AT_KEY: runtimes.now_ms()})

    status, _ = await harness.request("POST", f"/threads/{THREAD}{path}", body)

    assert status == 200
    assert [path for path, _ in harness.forwarded] == [
        f"/threads/{THREAD}/runs",
        f"/threads/{THREAD}/runs/remote-run/cancel",
    ]


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
            "user_id": "user-1",
        },
    }

    await harness.request("POST", f"/threads/{THREAD}/runs", run)

    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.runtime == "mda"
    assert indexed.metadata["runtime"] == "mda"
    assert harness.runtimes.mda.threads[THREAD]["metadata"] == indexed.metadata
    assert harness.runtimes.local.threads == {}
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
    # task messages the transcript records; a task message acts as no one.
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
    assert indexed.metadata["channel"] == "linear"
    assert harness.runtimes.local.threads == {}
    # MDA's channel creates it as its conversation's own thread; a copy made
    # here first would belong to the backend and keep the channel out.
    assert THREAD not in harness.runtimes.mda.threads


async def test_a_channel_conversations_worker_is_indexed_without_creating_it_on_mda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MDA's async subagent created the worker as the conversation's own thread, which
    the backend's own create can't reach: MDA answers it with 404."""
    harness = _Harness(monkeypatch)

    status, _ = await harness.request(
        "POST",
        "/threads",
        {
            "thread_id": THREAD,
            "metadata": {"channel": "slack", "owner_login": "ada"},
            "if_exists": "do_nothing",
        },
    )

    assert status == 200
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.metadata["channel"] == "slack"
    assert harness.runtimes.local.threads == {}
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
    ("path", "body"),
    [
        ("/runs", {"assistant_id": "agent", "input": {}}),
        ("/runs/run-1/cancel", None),
        ("/commands", {"id": 1, "method": "input.respond", "params": {}}),
    ],
)
async def test_a_channel_thread_takes_runs_stops_and_answers_as_its_conversation(
    monkeypatch: pytest.MonkeyPatch, secret: None, path: str, body: JsonValue
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, _ = await harness.request("POST", f"/threads/{THREAD}{path}", body)

    assert status == 200
    assert [forwarded for forwarded, _ in harness.forwarded] == [f"/threads/{THREAD}{path}"]
    # The conversation that owns the thread on MDA, read there; the thread stays its.
    assert harness.acted_as == [AS_THE_CONVERSATION]
    assert harness.read == [("GET", f"/threads/{THREAD}")]
    assert THREAD not in harness.runtimes.mda_runtime._mda_threads


async def test_a_channel_thread_takes_a_run_started_by_command(
    monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)
    command = {"id": 7, "method": "run.start", "params": {"assistant_id": "agent", "input": {}}}

    status, reply = await harness.request("POST", f"/threads/{THREAD}/commands", command)

    assert (status, reply) == (
        200,
        {"type": "success", "id": 7, "result": {"run_id": "remote-run"}},
    )
    assert harness.acted_as == [AS_THE_CONVERSATION]
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.status == "busy"


Resolved = list[tuple[str | None, str | None, ResolvePrincipalIdentity]]


@pytest.fixture
def agent_auth(monkeypatch: pytest.MonkeyPatch) -> Resolved:
    """Agent Auth, as it resolves an identity to a principal: what it was asked, with what."""
    monkeypatch.setenv("MDA_RUNTIME_API_KEY", "lsv2_sk_mda")
    monkeypatch.setenv("MDA_WORKSPACE_ID", WORKSPACE)
    resolved: Resolved = []

    class AgentAuth:
        def __init__(self, context: AgentAuthContext) -> None:
            self.context = context

        def resolve_principal(self, identity: ResolvePrincipalIdentity) -> str:
            resolved.append((self.context.api_key, self.context.workspace_id, identity))
            return PRINCIPAL

    monkeypatch.setattr(runtimes, "AgentAuth", AgentAuth)
    return resolved


def _acts_as(harness: _Harness) -> str | None:
    """The person the run token of the one run started says it acts as."""
    [(_, sent)] = harness.forwarded
    context = sent["context"]
    assert isinstance(context, dict) and isinstance(token := context["__run_token"], str)
    acting = verify_runtime_token(token).metadata.get(ACTING_USER_KEY)
    assert acting is None or isinstance(acting, str)
    return acting


async def test_a_persons_run_on_a_channel_thread_acts_as_them(
    monkeypatch: pytest.MonkeyPatch, secret: None, agent_auth: Resolved
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    # A queued follow-up: its run acts as the person who sent it.
    status, _ = await harness.request(
        "POST",
        f"/threads/{THREAD}/runs",
        {
            "assistant_id": "agent",
            "input": {},
            "metadata": {"user_id": USER_ID, ACTING_USER_KEY: USER_ID},
        },
    )

    assert status == 200
    assert harness.acted_as == [AS_THE_PERSON]
    assert _acts_as(harness) == USER_ID
    # The principal MDA resolves the user ``resolve_user`` names to, with MDA's key.
    assert agent_auth == [
        (
            "lsv2_sk_mda",
            WORKSPACE,
            {"provider": "trusted_backend", "issuer": WORKSPACE, "subject": USER_ID},
        )
    ]


@pytest.mark.parametrize(
    "command",
    [
        {"id": 7, "method": "run.start", "params": {"assistant_id": "agent", "input": {}}},
        {"id": 7, "method": "input.respond", "params": {}},
    ],
)
async def test_a_persons_command_on_a_channel_thread_acts_as_them(
    monkeypatch: pytest.MonkeyPatch,
    secret: None,
    agent_auth: Resolved,
    command: dict[str, JsonValue],
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    reply = await store.THREADS.command(THREAD, json.dumps(command).encode(), user_id=USER_ID)

    assert reply.status == 200
    assert harness.acted_as == [AS_THE_PERSON]


async def test_a_run_attributed_to_a_person_they_did_not_start_is_the_channels_own(
    monkeypatch: pytest.MonkeyPatch, secret: None, agent_auth: Resolved
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    # A wakeup, an event match or a task message: attributed to the person, not theirs.
    status, _ = await harness.request(
        "POST",
        f"/threads/{THREAD}/runs",
        {
            "assistant_id": "agent",
            "input": {},
            "metadata": {"user_id": USER_ID, "ls_attribution_identifier": "ada"},
        },
    )

    assert status == 200
    assert harness.acted_as == [AS_THE_CONVERSATION]
    assert _acts_as(harness) is None
    assert agent_auth == []


@pytest.mark.parametrize("command", [False, True], ids=["rest", "command"])
async def test_without_their_principal_a_persons_run_is_the_channels_own(
    monkeypatch: pytest.MonkeyPatch, secret: None, agent_auth: Resolved, command: bool
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    def unavailable(self: object, identity: ResolvePrincipalIdentity) -> str:
        raise CredentialStoreError("Agent Auth resolve principal failed", 503, {})

    monkeypatch.setattr(runtimes.AgentAuth, "resolve_principal", unavailable)
    params: dict[str, JsonValue] = {
        "assistant_id": "agent",
        "input": {},
        "metadata": {"user_id": USER_ID, ACTING_USER_KEY: USER_ID},
    }

    if command:
        start = {"id": 7, "method": "run.start", "params": params}
        reply = await store.THREADS.command(THREAD, json.dumps(start).encode(), user_id=USER_ID)
        status = reply.status
    else:
        status, _ = await harness.request("POST", f"/threads/{THREAD}/runs", params)

    # Never the user's id unresolved, which MDA would take for another person, and
    # never a token that names their connections for the channel's principal.
    assert status == 200
    assert harness.acted_as == [AS_THE_CONVERSATION]
    assert _acts_as(harness) is None


async def test_a_dashboard_command_on_a_channel_thread_is_its_senders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")
    ada = await User.sign_in("github", "1", login="ada")
    channel = {"channel": "linear"}
    machine = Principal(kind="api_key", workspace="acme")

    assert await proxy._sender_user_id(Principal.of_login("ada"), "ada", channel) == str(ada.id)
    # Only on a channel's thread, for a person Open SWE knows.
    assert await proxy._sender_user_id(Principal.of_login("ada"), "ada", {}) is None
    assert await proxy._sender_user_id(Principal.of_login("bob"), "bob", channel) is None
    # A machine is no person: the write is the channel's own.
    assert await proxy._sender_user_id(machine, "ada", channel) is None


@pytest.mark.parametrize(
    ("path", "body"),
    [("/copy", {}), ("/runs/crons", {"assistant_id": "agent", "schedule": "* * * * *"})],
)
async def test_a_channel_thread_is_neither_copied_nor_scheduled_here(
    monkeypatch: pytest.MonkeyPatch, path: str, body: JsonValue
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, reply = await harness.request("POST", f"/threads/{THREAD}{path}", body)

    assert status == 409
    assert isinstance(reply, dict) and "linear channel" in str(reply["detail"])
    assert harness.forwarded == []


async def test_stopping_every_run_on_a_channel_thread_acts_as_its_conversation(
    monkeypatch: pytest.MonkeyPatch, secret: None
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, _ = await harness.request(
        "POST", "/runs/cancel", {"thread_id": THREAD, "run_ids": ["run-1"]}
    )

    assert status == 200
    assert [forwarded for forwarded, _ in harness.forwarded] == ["/runs/cancel"]
    assert harness.acted_as == [AS_THE_CONVERSATION]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/state", {"values": {"messages": []}}),
        ("DELETE", "/runs/run-1", None),
        ("POST", "/commands", {"id": 1, "method": "input.inject", "params": {}}),
        ("POST", "/commands", {"id": 1, "method": "state.fork", "params": {}}),
    ],
)
async def test_a_channel_thread_refuses_other_writes_without_acting_as_its_conversation(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: JsonValue
) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", SERVICE_SECRET)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, reply = await harness.request(method, f"/threads/{THREAD}{path}", body)

    assert status == 409
    assert isinstance(reply, dict) and "linear channel" in str(reply["detail"])
    assert harness.forwarded == [] and harness.acted_as == [] and harness.read == []


async def test_without_the_service_secret_a_channel_thread_refuses_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MDA_SERVICE_SECRET", raising=False)
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, reply = await harness.request(
        "POST", f"/threads/{THREAD}/runs", {"assistant_id": "agent", "input": {}}
    )

    assert status == 409
    assert isinstance(reply, dict) and "linear channel" in str(reply["detail"])
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


async def test_a_channel_threads_metadata_stays_in_the_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(monkeypatch)
    await _channel_thread(harness)

    status, _ = await harness.request(
        "PATCH", f"/threads/{THREAD}", {"metadata": {"title": "Renamed"}}
    )
    await store.THREADS.copies.settled(THREAD)

    assert status == 200
    indexed = await index.get(THREAD)
    assert indexed is not None and indexed.metadata["title"] == "Renamed"
    assert harness.runtimes.local.threads == {}
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
    # A copy the Agent Server kept from before it was sealed.
    harness.runtimes.local.threads[THREAD] = {"thread_id": THREAD, "metadata": {}}
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
    assert harness.runtimes.local.threads == {}


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
    # A private conversation, of the person who reviews its PR.
    private = {"visibility": "private", "owner_type": "user", "owner_login": "ada"}
    await router.adopt_channel_run(THREAD, channel, metadata={"source": channel, **private})

    def reads(request: httpx.Request) -> httpx.Response:
        harness.read.append((request.method, request.url.path))
        if request.url.path == f"/threads/{THREAD}/state":
            return httpx.Response(200, json={"values": {"messages": _CHANNEL_CONVERSATION}})
        return httpx.Response(200, json={"values": {}})

    harness.runtimes.mda_runtime._read_http = httpx.AsyncClient(
        base_url=harness.runtimes.mda_runtime.url, transport=httpx.MockTransport(reads)
    )
    # The conversation's state is read as MDA's runtime reads it.
    reader = runtimes._MdaThreads(harness.runtimes.mda_runtime)
    monkeypatch.setattr(harness.runtimes.mda, "state", reader.state, raising=False)
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
    # Its history stays as private on the PR's thread.
    assert {key: pr_metadata.get(key) for key in private} == private
    # The channel's thread got no run, and the review its 👀 only once one started.
    assert all(f"/threads/{THREAD}/runs" not in path for path, _ in harness.forwarded)
    react.assert_awaited_once()
