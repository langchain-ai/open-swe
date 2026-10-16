import time

import jwt
import pytest
from starlette.types import Message, Receive, Scope, Send

from openswe.remote_runtime.server import RunTokenMiddleware
from openswe.remote_runtime.tokens import (
    RemoteRun,
    RuntimeTokenError,
    sign_runtime_token,
    verify_runtime_token,
)
from openswe.users import UserPreferences

RUN = RemoteRun(
    thread_id="thread-1",
    assistant_id="reviewer",
    configurable={"repo": {"owner": "acme", "name": "widgets"}, "pr_number": 7},
)


@pytest.fixture(autouse=True)
def token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REMOTE_RUNTIME_TOKEN_SECRET",
        "current-signing-key-0123456789abcdef,previous-signing-key-0123456789abcdef",
    )


def test_run_token_round_trips_the_signed_run() -> None:
    assert verify_runtime_token(sign_runtime_token(RUN)) == RUN


def test_run_token_signed_with_a_rotated_out_key_still_verifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "previous-signing-key-0123456789abcdef")
    token = sign_runtime_token(RUN)
    monkeypatch.setenv(
        "REMOTE_RUNTIME_TOKEN_SECRET",
        "current-signing-key-0123456789abcdef,previous-signing-key-0123456789abcdef",
    )

    assert verify_runtime_token(token).thread_id == "thread-1"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(
            jwt.encode(
                {"sub": "thread-1", "cfg": {}},
                "current-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="missing-claims",
        ),
        pytest.param(
            jwt.encode(
                {
                    "iss": "open-swe",
                    "aud": "open-swe-remote-runtime",
                    "iat": 0,
                    "exp": 1,
                    "sub": "thread-1",
                    "assistant_id": "reviewer",
                    "cfg": {},
                },
                "current-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="expired",
        ),
        pytest.param(
            jwt.encode(
                {
                    "iss": "open-swe",
                    "aud": "open-swe-remote-runtime",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 60,
                    "sub": "thread-1",
                    "assistant_id": "reviewer",
                    "cfg": {},
                },
                "someone-elses-signing-key-0123456789abcdef",
                algorithm="HS256",
            ),
            id="foreign-key",
        ),
    ],
)
def test_run_token_refuses_tokens_this_backend_did_not_issue(token: str) -> None:
    with pytest.raises(RuntimeTokenError):
        verify_runtime_token(token)


async def _status(authorization: str | None) -> tuple[int, RemoteRun | None]:
    seen: list[RemoteRun] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["state"]["remote_run"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers = [(b"authorization", authorization.encode())] if authorization else []
    scope: Scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(message)

    await RunTokenMiddleware(inner)(scope, receive, send)
    return sent[0]["status"], (seen[0] if seen else None)


async def test_tool_server_refuses_calls_without_a_valid_run_token() -> None:
    assert await _status(None) == (401, None)
    assert await _status("Bearer not-a-token") == (401, None)


async def test_tool_server_hands_the_verified_run_to_its_handlers() -> None:
    assert await _status(f"Bearer {sign_runtime_token(RUN)}") == (200, RUN)


async def test_mda_catalog_leaves_integrations_and_code_mode_to_mda() -> None:
    """Seldom-needed tools go to MDA's code mode, not the model's schemas; the graph's own stays here."""
    from langchain.agents import create_agent
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.tools import StructuredTool
    from langchain_quickjs import CodeInterpreterMiddleware

    from openswe.middleware.dynamic_tools import DynamicToolMiddleware
    from openswe.remote_runtime.graph import RemoteGraph
    from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, ToolSurface

    def integration(name: str) -> StructuredTool:
        async def run(query: str) -> str:
            return query

        return StructuredTool.from_function(coroutine=run, name=name, description=name)

    async def build(config: object, surface: ToolSurface) -> None:
        surface.dynamic = DynamicToolMiddleware(
            {"MCP": [integration("mcp_search"), integration("mcp.dotted")]}
        )
        core = [integration(name) for name in ("read_file", "manage_thread", "slack_reply")]
        middleware: list[AgentMiddleware] = [
            surface.dynamic,
            CodeInterpreterMiddleware(ptc=[integration("mcp_search")]),
        ]
        surface.graph = create_agent(
            FakeListChatModel(responses=[]), tools=core, middleware=middleware
        )
        surface.code_mode = True
        surface.middleware = middleware
        choice = ModelChoice("openai:gpt-6.1-sol", {})
        surface.models = SurfaceModels(main=choice, subagent=choice, use_gateway=False)

    async def start_models(cfg: object) -> object:
        raise AssertionError("not asked for")

    graph = RemoteGraph("agent", build, start_models)
    catalog = await graph.hook(RUN, "catalog", {"run_id": "run-1"})

    assert isinstance(catalog, dict)
    tools = catalog["tools"]
    assert isinstance(tools, list)
    names = {tool["name"] for tool in tools if isinstance(tool, dict)}
    assert {"mcp_search", "mcp.dotted", "read_file", "manage_thread", "slack_reply"} <= names
    assert "eval" not in names
    assert catalog["code_only"] == ["manage_thread", "mcp_search"]
    assert catalog["also_from_code"] == ["read_file"]


class _LocalThreads:
    """The local Agent Server's threads, as the MDA proxy reads and claims them."""

    def __init__(self, threads: dict[str, dict[str, object]]) -> None:
        self.threads = threads

    async def get(self, thread_id: str) -> dict[str, object]:
        return self.threads[thread_id]

    async def update(self, thread_id: str, *, metadata: dict[str, object]) -> None:
        self.threads[thread_id]["metadata"] = {**self.threads[thread_id]["metadata"], **metadata}


class _Client:
    def __init__(self, threads: object) -> None:
        self.threads = threads


class _RemoteThreads:
    async def create(self, **_: object) -> None:
        return None


async def _through_proxy(
    monkeypatch: pytest.MonkeyPatch,
    local: dict[str, dict[str, object]],
    method: str,
    path: str,
    body: dict[str, object],
) -> tuple[list[str], list[dict[str, object]]]:
    """Send one request through the MDA proxy; return what reached here and what reached MDA."""
    import json

    import httpx

    from openswe.remote_runtime import proxy

    async def context(
        configurable: dict[str, object], *, thread_id: str, assistant_id: str
    ) -> dict[str, object]:
        token = sign_runtime_token(
            RemoteRun(thread_id=thread_id, assistant_id=assistant_id, configurable=configurable)
        )
        return {"graph": assistant_id, "__run_token": token}

    monkeypatch.setenv("MDA_RUNTIME_URL", "http://localhost:2041")
    monkeypatch.setattr(proxy, "remote_run_context", context)
    # LangSmith caches the project it read from the environment.
    monkeypatch.setattr(proxy, "get_tracer_project", lambda: "open-swe-e2e")
    monkeypatch.setattr(
        proxy.MdaRuntime, "local", staticmethod(lambda: _Client(_LocalThreads(local)))
    )
    monkeypatch.setattr(proxy.MdaRuntime, "sdk", lambda _self: _Client(_RemoteThreads()))

    async def preferences(login: str) -> UserPreferences:
        return UserPreferences(run_on_mda=login in MDA_LOGINS)

    monkeypatch.setattr(proxy.User, "preferences_for_login", preferences)

    async def workspace_settings(workspace: str | None) -> dict[str, object]:
        return {"run_on_mda": workspace in MDA_WORKSPACES}

    monkeypatch.setattr(proxy, "cached_workspace_settings", workspace_settings)
    forwarded: list[dict[str, object]] = []

    def mda(request: httpx.Request) -> httpx.Response:
        forwarded.append({"path": request.url.path, "body": json.loads(request.content or b"{}")})
        return httpx.Response(200, json={"run_id": "remote-run"})

    reached_here: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        reached_here.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    middleware = proxy.MdaThreadProxy(app)
    runtime = middleware.runtime()
    assert runtime is not None
    runtime._http = httpx.AsyncClient(base_url=runtime.url, transport=httpx.MockTransport(mda))
    payload = json.dumps(body).encode()

    async def receive() -> Message:
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(_message: Message) -> None:
        return None

    scope: Scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }
    await middleware(scope, receive, send)
    return reached_here, forwarded


FRESH_THREAD = "00000000-0000-4000-8000-000000000001"
LEGACY_THREAD = "00000000-0000-4000-8000-000000000002"
MDA_LOGINS = frozenset({"ada"})
MDA_WORKSPACES = frozenset({"platform"})


def _fresh_run(owner: str, starter: str) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    local: dict[str, dict[str, object]] = {
        FRESH_THREAD: {
            "thread_id": FRESH_THREAD,
            "status": "idle",
            "values": None,
            "metadata": {"owner_login": owner},
        }
    }
    run: dict[str, object] = {
        "assistant_id": "agent",
        "input": {"messages": []},
        "config": {
            "configurable": {
                "repo": {"owner": "acme", "name": "widgets"},
                "github_login": starter,
                "__event_streaming_v2": True,
            }
        },
    }
    return local, run


async def test_mda_proxy_claims_a_fresh_thread_and_signs_its_run_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, run_body = _fresh_run(owner="ada", starter="ada")

    reached_here, forwarded = await _through_proxy(
        monkeypatch, local, "POST", f"/threads/{FRESH_THREAD}/runs", run_body
    )

    assert reached_here == []
    assert local[FRESH_THREAD]["metadata"] == {"owner_login": "ada", "runtime": "mda"}
    [run] = forwarded
    sent = run["body"]
    assert isinstance(sent, dict)
    assert run["path"] == f"/threads/{FRESH_THREAD}/runs"
    assert sent["assistant_id"] == "open-swe"
    # Nothing the remote graph is handed carries the configurable but the signed token.
    assert "config" not in sent
    assert sent["langsmith_tracer"] == {"project_name": "open-swe-e2e"}
    context = sent["context"]
    assert context["__event_streaming_v2"] is True
    assert verify_runtime_token(context["__run_token"]).configurable == {
        "repo": {"owner": "acme", "name": "widgets"},
        "github_login": "ada",
    }


async def test_mda_proxy_acts_only_on_requests_the_agent_server_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langgraph_api.auth import middleware as auth
    from starlette.authentication import AuthenticationBackend, AuthenticationError
    from starlette.middleware import Middleware
    from starlette.requests import HTTPConnection

    class RejectEveryone(AuthenticationBackend):
        async def authenticate(self, conn: HTTPConnection) -> None:
            raise AuthenticationError("Missing authentication headers")

    monkeypatch.setattr(
        auth,
        "auth_middleware",
        Middleware(
            auth.ConditionalAuthenticationMiddleware,
            backend=RejectEveryone(),
            on_error=auth.on_error,
        ),
    )
    local, run_body = _fresh_run(owner="ada", starter="ada")

    reached_here, forwarded = await _through_proxy(
        monkeypatch, local, "POST", f"/threads/{FRESH_THREAD}/runs", run_body
    )

    assert reached_here == []
    assert forwarded == []
    assert local[FRESH_THREAD]["metadata"] == {"owner_login": "ada"}


@pytest.mark.parametrize(
    ("permissions", "is_forwarded"), [([], False), (["openswe:backend"], True)]
)
async def test_mda_proxy_forwards_only_the_backend_under_custom_auth(
    monkeypatch: pytest.MonkeyPatch, permissions: list[str], is_forwarded: bool
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
    local, run_body = _fresh_run(owner="ada", starter="ada")

    reached_here, forwarded = await _through_proxy(
        monkeypatch, local, "POST", f"/threads/{FRESH_THREAD}/runs", run_body
    )

    assert bool(forwarded) is is_forwarded
    assert reached_here == ([] if is_forwarded else [f"/threads/{FRESH_THREAD}/runs"])


@pytest.mark.parametrize(("workspace", "claimed"), [("platform", True), ("default", False)])
async def test_mda_proxy_runs_threads_no_person_starts_where_their_workspace_chose(
    monkeypatch: pytest.MonkeyPatch, workspace: str, claimed: bool
) -> None:
    local: dict[str, dict[str, object]] = {
        FRESH_THREAD: {
            "thread_id": FRESH_THREAD,
            "status": "idle",
            "values": None,
            "metadata": {"owner_type": "system", "workspace": workspace},
        }
    }
    run_body = {"assistant_id": "reviewer", "input": None, "config": {"configurable": {}}}

    reached_here, forwarded = await _through_proxy(
        monkeypatch, local, "POST", f"/threads/{FRESH_THREAD}/runs", run_body
    )

    assert bool(forwarded) is claimed
    assert reached_here == ([] if claimed else [f"/threads/{FRESH_THREAD}/runs"])


async def test_mda_proxy_runs_threads_here_unless_their_owner_chose_mda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The owner decides, even when someone who chose MDA starts the run.
    local, run_body = _fresh_run(owner="bob", starter="ada")

    reached_here, forwarded = await _through_proxy(
        monkeypatch, local, "POST", f"/threads/{FRESH_THREAD}/runs", run_body
    )

    assert reached_here == [f"/threads/{FRESH_THREAD}/runs"]
    assert forwarded == []
    assert local[FRESH_THREAD]["metadata"] == {"owner_login": "bob"}


async def test_mda_proxy_leaves_threads_with_local_history_where_they_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = {
        LEGACY_THREAD: {
            "thread_id": LEGACY_THREAD,
            "status": "idle",
            "values": {"messages": [{"type": "human", "content": "hi"}]},
            "metadata": {},
        }
    }

    started, forwarded = await _through_proxy(
        monkeypatch,
        local,
        "POST",
        f"/threads/{LEGACY_THREAD}/runs",
        {"assistant_id": "agent", "input": None, "config": {"configurable": {}}},
    )
    read, read_forwarded = await _through_proxy(
        monkeypatch, local, "GET", f"/threads/{LEGACY_THREAD}/state", {}
    )

    assert started == [f"/threads/{LEGACY_THREAD}/runs"]
    assert read == [f"/threads/{LEGACY_THREAD}/state"]
    assert forwarded == read_forwarded == []
    assert local[LEGACY_THREAD]["metadata"] == {}


class _LocalSearch:
    """The local Agent Server's thread search: status, ids, newest first, paged."""

    def __init__(self, threads: list[dict[str, object]]) -> None:
        self.threads = threads

    async def post(self, path: str, *, json: dict[str, object]) -> list[dict[str, object]]:
        assert path == "/threads/search"
        status, ids = json.get("status"), json.get("ids")
        found = [
            thread
            for thread in self.threads
            if (status is None or thread["status"] == status)
            and (not isinstance(ids, list) or thread["thread_id"] in ids)
        ]
        found.sort(key=lambda thread: str(thread["created_at"]), reverse=True)
        offset = int(json.get("offset") or 0)
        return found[offset : offset + int(json.get("limit") or 10)]


class _RemoteSearch:
    """The MDA deployment's threads: each claimed thread that has run, by status."""

    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses

    async def search(self, *, status: str, limit: int, offset: int) -> list[dict[str, object]]:
        found = [{"thread_id": t} for t, s in self.statuses.items() if s == status]
        return found[offset : offset + limit]

    async def get(self, thread_id: str) -> dict[str, object]:
        import httpx
        from langgraph_sdk.errors import NotFoundError

        if thread_id not in self.statuses:
            response = httpx.Response(404, request=httpx.Request("GET", "http://mda"))
            raise NotFoundError("not found", response=response, body=None)
        return {"status": self.statuses[thread_id], "values": {}, "interrupts": {}, "error": None}


def _thread(thread_id: str, status: str, created_at: str, *, claimed: bool) -> dict[str, object]:
    metadata = {"runtime": "mda"} if claimed else {}
    return {
        "thread_id": thread_id,
        "status": status,
        "created_at": created_at,
        "metadata": metadata,
    }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"status": "busy"}, [("local-busy", "busy"), ("claimed-busy", "busy")]),
        (
            {"status": "idle"},
            [("local-idle", "idle"), ("claimed-idle", "idle"), ("claimed-unrun", "idle")],
        ),
        ({"status": "idle", "limit": 1, "offset": 1}, [("claimed-idle", "idle")]),
    ],
    ids=["busy-adds-claimed", "idle-drops-busy-claimed", "idle-paged-after-merge"],
)
async def test_mda_proxy_status_search_counts_claimed_threads_by_their_mda_status(
    monkeypatch: pytest.MonkeyPatch,
    query: dict[str, object],
    expected: list[tuple[str, str]],
) -> None:
    import json

    from openswe.remote_runtime import proxy

    # Claimed threads stay idle here; their real status lives on MDA.
    local = [
        _thread("local-busy", "busy", "5", claimed=False),
        _thread("local-idle", "idle", "4", claimed=False),
        _thread("claimed-busy", "idle", "3", claimed=True),
        _thread("claimed-idle", "idle", "2", claimed=True),
        _thread("claimed-unrun", "idle", "1", claimed=True),
    ]
    remote = _RemoteSearch({"claimed-busy": "busy", "claimed-idle": "idle"})
    monkeypatch.setenv("MDA_RUNTIME_URL", "http://localhost:2041")

    class _LocalClient:
        http = _LocalSearch(local)

    monkeypatch.setattr(proxy.MdaRuntime, "local", staticmethod(lambda: _LocalClient()))
    monkeypatch.setattr(proxy.MdaRuntime, "sdk", lambda _self: _Client(remote))
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("a search is answered by the proxy")

    async def receive() -> Message:
        return {"type": "http.request", "body": json.dumps(query).encode(), "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/threads/search",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }
    await proxy.MdaThreadProxy(app)(scope, receive, send)

    page = json.loads(sent[-1]["body"])
    assert [(thread["thread_id"], thread["status"]) for thread in page] == expected
