"""Tests for the pre-run credential gate."""

from __future__ import annotations

import asyncio
import threading
import time as time_module
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

import managed_deepagents._connections as connections_mod
import managed_deepagents._credential_gate as gate_mod
from managed_deepagents._agent_auth import (
    AgentAuthContext,
    CredentialAuthorizationRequiredError,
    WorkspaceConnection,
)
from managed_deepagents._connections import CredentialStoreError, connections
from managed_deepagents._credential_gate import (
    CREDENTIAL_GATE_MIDDLEWARE_NAME,
    CredentialConsumer,
    clear_credential_tool_cache_for_tests,
    create_credential_gate_middleware,
)

#: A stable declaration object, as a ``connectors/`` module would export.
DECLARATION = object()


class _FakeClock:
    """Stands in for the ``time`` module the cache reads its deadline from."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _InterruptedError(RuntimeError):
    """Stands in for ``langgraph.types.interrupt`` raising on a fresh entry."""


@dataclass
class _FakeAgentAuth:
    workspace: dict[str, tuple[str, str]]
    granted: dict[str, str]
    reads: list[str]
    sessions: list[str]
    context: AgentAuthContext = field(
        default_factory=lambda: AgentAuthContext(base_url="https://aa.example", principal_id="p-1")
    )

    def find_workspace_connection(self, slug: str) -> WorkspaceConnection | None:
        row = self.workspace.get(slug)
        if row is None:
            return None
        kind, owner_policy = row
        return WorkspaceConnection(
            connection_id=f"conn-{slug}",
            kind=kind,
            owner_policy=owner_policy,
        )

    def read_user_access_token(self, slug: str) -> str | None:
        self.reads.append(slug)
        row = self.workspace.get(slug)
        if row is None or row[0] != "oauth2":
            return None
        return self.granted.get(slug)

    def read_user_access_token_for_connection(self, connection: WorkspaceConnection) -> str | None:
        slug = connection.connection_id.removeprefix("conn-")
        return self.read_user_access_token(slug)

    def read_user_secret(self, slug: str) -> str | None:
        self.reads.append(slug)
        row = self.workspace.get(slug)
        if row is None or row[0] != "secret":
            return None
        return self.granted.get(slug)

    def read_user_secret_for_connection(self, connection: WorkspaceConnection) -> str | None:
        slug = connection.connection_id.removeprefix("conn-")
        return self.read_user_secret(slug)

    def start_authorization_session(self, slug: str) -> dict[str, str]:
        self.sessions.append(slug)
        return {
            "slug": slug,
            "connect_url": f"https://idp.example/{slug}",
            "auth_id": f"sess_{slug}",
        }


def _oauth_workspace(*slugs: str) -> dict[str, tuple[str, str]]:
    return dict.fromkeys(slugs, ("oauth2", "user"))


@pytest.fixture
def agent_auth(monkeypatch: pytest.MonkeyPatch) -> _FakeAgentAuth:
    fake = _FakeAgentAuth(workspace={}, granted={}, reads=[], sessions=[])
    monkeypatch.setattr(gate_mod, "AgentAuth", lambda _context: fake)
    monkeypatch.setattr(gate_mod, "with_managed_runtime", lambda tool: tool)
    return fake


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    clear_credential_tool_cache_for_tests()
    yield
    clear_credential_tool_cache_for_tests()


@pytest.fixture
def interrupts(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    seen: list[object] = []

    def _interrupt(payload: object) -> None:
        seen.append(payload)
        raise _InterruptedError

    monkeypatch.setattr(gate_mod, "_interrupt", _interrupt)
    return seen


def _as_caller(monkeypatch: pytest.MonkeyPatch, user_id: str | None) -> None:
    identity = None if user_id is None else {"user": {"kind": "person", "id": user_id}}
    monkeypatch.setattr(gate_mod, "build_runtime_identity", lambda _configurable: identity)
    monkeypatch.setattr(gate_mod, "resolve_runtime_configurable", lambda runtime: runtime)
    monkeypatch.setattr(
        gate_mod,
        "resolve_agent_auth_context",
        lambda ident: AgentAuthContext(
            base_url="https://api.smith.langchain.com",
            agent_id="deployment-1",
            principal_id=ident["user"]["id"],
        ),
    )


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _ModelRequest:
    def __init__(self, tools: Sequence[object] | None = None) -> None:
        self.tools = list(tools or [])
        self.runtime = {}

    def override(self, **changes: object) -> _ModelRequest:
        request = _ModelRequest(self.tools)
        for key, value in changes.items():
            setattr(request, key, value)
        return request


class _ToolCallRequest:
    def __init__(self, name: str) -> None:
        self.tool = None
        self.tool_call = {"name": name}
        self.runtime = {}

    def override(self, **changes: object) -> _ToolCallRequest:
        request = _ToolCallRequest("")
        request.tool_call = self.tool_call
        for key, value in changes.items():
            setattr(request, key, value)
        return request


def _consumer(
    tools: Sequence[str] = ("notion__search",),
    slugs: tuple[str, ...] = ("notion",),
    owner: object | None = None,
    calls: list[Mapping[str, str]] | None = None,
) -> CredentialConsumer:
    def load(access_tokens: Mapping[str, str], _context: AgentAuthContext) -> list[object]:
        if calls is not None:
            calls.append(dict(access_tokens))
        return [_Tool(name) for name in tools]

    references = tuple(connections.get(slug, owner or {"type": "user"}) for slug in slugs)
    return CredentialConsumer(declaration=DECLARATION, credentials=references, load=load)


def test_no_consumer_installs_no_middleware() -> None:
    assert create_credential_gate_middleware([]) is None


def test_middleware_is_named_for_credentials() -> None:
    middleware = create_credential_gate_middleware([_consumer()])

    assert middleware is not None
    assert middleware.name == CREDENTIAL_GATE_MIDDLEWARE_NAME
    assert CREDENTIAL_GATE_MIDDLEWARE_NAME == "mda-credential-gate"


def test_declared_owner_that_is_not_the_caller_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    middleware = create_credential_gate_middleware(
        [_consumer(owner={"type": "user", "id": "someone-else"})]
    )
    assert middleware is not None

    with pytest.raises(CredentialStoreError, match="must match the authenticated principal"):
        middleware.before_agent(None, {})
    assert agent_auth.reads == []


def test_declared_owner_that_is_the_caller_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    middleware = create_credential_gate_middleware(
        [_consumer(owner={"type": "user", "id": "principal-1"})]
    )
    assert middleware is not None

    middleware.before_agent(None, {})

    assert interrupts == []


def test_granted_caller_passes_through_and_loads_tools(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    middleware = create_credential_gate_middleware([_consumer()])
    assert middleware is not None

    middleware.before_agent(None, {})

    assert interrupts == []
    request = middleware.wrap_model_call(_ModelRequest([_Tool("authored")]), lambda seen: seen)
    assert [tool.name for tool in request.tools] == ["authored", "notion__search"]


def test_missing_grants_interrupt_once_with_connect_urls(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion", "linear")
    middleware = create_credential_gate_middleware([_consumer(slugs=("notion", "linear"))])
    assert middleware is not None

    with pytest.raises(_InterruptedError):
        middleware.before_agent(None, {})

    assert interrupts == [
        {
            "type": "credential_authorization_required",
            "message": "Connect the following integrations to continue.",
            "credentials": [
                {
                    "slug": "notion",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/notion",
                    "auth_id": "sess_notion",
                },
                {
                    "slug": "linear",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/linear",
                    "auth_id": "sess_linear",
                },
            ],
        }
    ]


def test_gate_merges_connection_and_managed_tools_pending_into_one_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    managed_tools_calls: list[str] = []

    def collect_pending_credentials(_context: AgentAuthContext) -> list[dict[str, str]]:
        managed_tools_calls.append("collected")
        return [
            {
                "slug": "mt-linear",
                "kind": "oauth2",
                "connect_url": "https://idp.example/linear",
                "auth_id": "sess-linear",
            }
        ]

    base = _consumer(slugs=("notion",))
    consumer = CredentialConsumer(
        declaration=base.declaration,
        credentials=base.credentials,
        load=base.load,
        collect_pending_credentials=collect_pending_credentials,
    )
    middleware = create_credential_gate_middleware([consumer])
    assert middleware is not None

    with pytest.raises(_InterruptedError):
        middleware.before_agent(None, {})

    assert managed_tools_calls == ["collected"]
    assert interrupts == [
        {
            "type": "credential_authorization_required",
            "message": "Connect the following integrations to continue.",
            "credentials": [
                {
                    "slug": "notion",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/notion",
                    "auth_id": "sess_notion",
                },
                {
                    "slug": "mt-linear",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/linear",
                    "auth_id": "sess-linear",
                },
            ],
        }
    ]


def test_gate_collects_managed_tools_when_no_connection_credentials(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")

    def collect_pending_credentials(_context: AgentAuthContext) -> list[dict[str, str]]:
        return [
            {
                "slug": "mt-notion",
                "kind": "oauth2",
                "connect_url": "https://idp.example/notion",
                "auth_id": "sess-notion",
            }
        ]

    consumer = CredentialConsumer(
        declaration=DECLARATION,
        credentials=(),
        load=lambda _tokens, _ctx: [],
        requires_identity=False,
        collect_pending_credentials=collect_pending_credentials,
    )
    middleware = create_credential_gate_middleware([consumer])
    assert middleware is not None

    with pytest.raises(_InterruptedError):
        middleware.before_agent(None, {})

    assert interrupts == [
        {
            "type": "credential_authorization_required",
            "message": "Connect the following integrations to continue.",
            "credentials": [
                {
                    "slug": "mt-notion",
                    "kind": "oauth2",
                    "connect_url": "https://idp.example/notion",
                    "auth_id": "sess-notion",
                }
            ],
        }
    ]


def test_a_replayed_node_mints_one_session_then_stops(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
) -> None:
    _as_caller(monkeypatch, "principal-1")
    # A replayed node sees `interrupt` return the pending resume value.
    monkeypatch.setattr(gate_mod, "_interrupt", lambda _payload: None)
    agent_auth.workspace = _oauth_workspace("notion")
    middleware = create_credential_gate_middleware([_consumer()])
    assert middleware is not None

    with pytest.raises(CredentialAuthorizationRequiredError, match="has not connected: notion"):
        middleware.before_agent(None, {})

    assert agent_auth.reads == ["notion"]
    assert agent_auth.sessions == ["notion"]


@pytest.mark.parametrize("user_id", [None, ""])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_missing_caller_gets_sign_in_guidance(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
    user_id: str | None,
    asynchronous: bool,
) -> None:
    _as_caller(monkeypatch, user_id)
    loads: list[Mapping[str, str]] = []
    middleware = create_credential_gate_middleware(
        [_consumer(slugs=("notion", "linear", "notion"), calls=loads)]
    )
    assert middleware is not None

    result = (
        asyncio.run(middleware.abefore_agent(None, {}))
        if asynchronous
        else middleware.before_agent(None, {})
    )

    assert result["jump_to"] == "end"
    message = result["messages"][0]
    assert message["role"] == "assistant"
    assert "notion, linear" in message["content"]
    assert message["content"].count("notion") == 1
    assert "Retry as an authenticated user" in message["content"]
    assert agent_auth.reads == []
    assert agent_auth.sessions == []
    assert loads == []
    assert interrupts == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_missing_caller_ends_graph_without_model_or_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    asynchronous: bool,
) -> None:
    agents = pytest.importorskip("langchain.agents")
    fake_models = pytest.importorskip("langchain_core.language_models.fake_chat_models")
    _as_caller(monkeypatch, None)
    loads: list[Mapping[str, str]] = []
    middleware = create_credential_gate_middleware([_consumer(calls=loads)])
    # An empty response list makes any unexpected model invocation fail.
    graph = agents.create_agent(
        model=fake_models.FakeListChatModel(responses=[]), middleware=[middleware]
    )
    state = {"messages": [{"role": "user", "content": "Search my documents."}]}

    result = asyncio.run(graph.ainvoke(state)) if asynchronous else graph.invoke(state)

    assert result["messages"][-1].type == "ai"
    assert "notion" in result["messages"][-1].content
    assert not result.get("__interrupt__")
    assert loads == []
    assert agent_auth.reads == []
    assert agent_auth.sessions == []


def test_unauthenticated_run_skips_optional_tools(
    monkeypatch: pytest.MonkeyPatch, agent_auth: _FakeAgentAuth
) -> None:
    _as_caller(monkeypatch, None)
    base = _consumer(slugs=())
    optional = CredentialConsumer(
        declaration=base.declaration,
        credentials=(),
        load=base.load,
        requires_identity=False,
    )
    middleware = create_credential_gate_middleware([optional])
    assert middleware is not None

    with pytest.warns(RuntimeWarning, match="skipped"):
        middleware.before_agent(None, {})


def test_channel_event_runs_without_integrations_a_person_must_connect(
    monkeypatch: pytest.MonkeyPatch, agent_auth: _FakeAgentAuth, interrupts: list[object]
) -> None:
    identity = {"user": {"kind": "service", "id": "mda:channel"}, "source": {"provider": "github"}}
    monkeypatch.setattr(gate_mod, "build_runtime_identity", lambda _configurable: identity)
    monkeypatch.setattr(gate_mod, "resolve_runtime_configurable", lambda runtime: runtime)
    calls: list[Mapping[str, str]] = []
    middleware = create_credential_gate_middleware([_consumer(calls=calls)])
    assert middleware is not None

    with pytest.warns(RuntimeWarning, match="a github event runs as the channel"):
        assert middleware.before_agent(None, {}) is None
    seen: list[_ModelRequest] = []
    middleware.wrap_model_call(_ModelRequest(), seen.append)

    assert seen[0].tools == []
    assert calls == []
    assert agent_auth.reads == []
    assert agent_auth.sessions == []
    assert interrupts == []


def test_studio_resume_on_a_channel_conversation_runs_without_user_integrations(
    monkeypatch: pytest.MonkeyPatch, agent_auth: _FakeAgentAuth, interrupts: list[object]
) -> None:
    identity = {"user": {"kind": "person", "id": "studio-alice"}, "source": {"provider": "studio"}}
    monkeypatch.setattr(gate_mod, "build_runtime_identity", lambda _configurable: identity)
    monkeypatch.setattr(gate_mod, "resolve_runtime_configurable", lambda runtime: runtime)
    monkeypatch.setattr(connections_mod, "on_channel_thread", lambda: True)
    calls: list[Mapping[str, str]] = []
    middleware = create_credential_gate_middleware([_consumer(calls=calls)])
    assert middleware is not None

    with pytest.warns(RuntimeWarning, match="continues a channel's conversation"):
        assert middleware.before_agent(None, {}) is None
    seen: list[_ModelRequest] = []
    middleware.wrap_model_call(_ModelRequest(), seen.append)

    assert seen[0].tools == []
    assert calls == []
    assert agent_auth.reads == []
    assert agent_auth.sessions == []
    assert interrupts == []


def test_github_sender_runs_without_integrations_they_would_have_to_connect(
    monkeypatch: pytest.MonkeyPatch, agent_auth: _FakeAgentAuth, interrupts: list[object]
) -> None:
    identity = {"user": {"kind": "person", "id": "github:7"}, "source": {"provider": "github"}}
    monkeypatch.setattr(gate_mod, "build_runtime_identity", lambda _configurable: identity)
    monkeypatch.setattr(gate_mod, "resolve_runtime_configurable", lambda runtime: runtime)
    calls: list[Mapping[str, str]] = []
    middleware = create_credential_gate_middleware([_consumer(calls=calls)])
    assert middleware is not None

    with pytest.warns(RuntimeWarning, match="does not map its github sender to a user"):
        assert middleware.before_agent(None, {}) is None
    seen: list[_ModelRequest] = []
    middleware.wrap_model_call(_ModelRequest(), seen.append)

    assert seen[0].tools == []
    assert calls == []
    assert agent_auth.sessions == []
    assert interrupts == []


def test_linked_slack_person_keeps_integrations_on_their_channel_conversation(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    identity = {
        "user": {"kind": "person", "id": "principal-1"},
        "source": {"provider": "slack", "thread_id": "thread-1"},
    }
    monkeypatch.setattr(gate_mod, "build_runtime_identity", lambda _configurable: identity)
    monkeypatch.setattr(connections_mod, "on_channel_thread", lambda: True)
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    loads: list[Mapping[str, str]] = []
    middleware = create_credential_gate_middleware([_consumer(calls=loads)])
    assert middleware is not None

    middleware.before_agent(None, {})

    assert loads == [{"notion": "ya29.caller"}]
    assert interrupts == []


def test_tools_survive_a_resume_that_rebuilds_the_middleware(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    loads: list[Mapping[str, str]] = []

    first = create_credential_gate_middleware([_consumer(calls=loads)])
    assert first is not None
    first.before_agent(None, {})

    # A resumed run re-enters through a fresh graph factory, so the middleware
    # instance that served the first turn is gone.
    resumed = create_credential_gate_middleware([_consumer(calls=loads)])
    assert resumed is not None
    request = resumed.wrap_model_call(_ModelRequest(), lambda seen: seen)

    assert [tool.name for tool in request.tools] == ["notion__search"]
    assert loads == [{"notion": "ya29.caller"}]


def test_tool_call_resolves_without_a_preceding_model_call(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    middleware = create_credential_gate_middleware([_consumer()])
    assert middleware is not None

    request = middleware.wrap_tool_call(_ToolCallRequest("notion__search"), lambda seen: seen)

    assert request.tool is not None
    assert request.tool.name == "notion__search"


def test_one_caller_never_sees_another_callers_tools(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    middleware = create_credential_gate_middleware([_consumer()])
    assert middleware is not None

    _as_caller(monkeypatch, "alice")
    middleware.before_agent(None, {})
    _as_caller(monkeypatch, "bob")
    middleware.before_agent(None, {})

    # Two callers, so the declaration is loaded once per principal.
    assert agent_auth.reads == ["notion", "notion", "notion", "notion"]


def test_a_lapsed_cache_entry_is_reloaded(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    clock = _FakeClock()
    monkeypatch.setattr(gate_mod, "time", clock)
    loads: list[Mapping[str, str]] = []
    middleware = create_credential_gate_middleware([_consumer(calls=loads)])
    assert middleware is not None

    middleware.before_agent(None, {})
    assert len(loads) == 1

    middleware.before_agent(None, {})
    assert len(loads) == 1

    clock.now = gate_mod._TOOL_CACHE_TTL_SECONDS * 2
    middleware.before_agent(None, {})

    assert len(loads) == 2


def test_threads_racing_a_lapsed_entry_load_once(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    clock = _FakeClock()
    monkeypatch.setattr(gate_mod, "time", clock)
    started = threading.Barrier(2)
    loads: list[Mapping[str, str]] = []

    def load(access_tokens: Mapping[str, str], _context: AgentAuthContext) -> list[object]:
        loads.append(dict(access_tokens))
        # Hold the load open so a second thread reaches the cache meanwhile.
        time_module.sleep(0.05)
        return [_Tool("notion__search")]

    consumer = CredentialConsumer(
        declaration=DECLARATION,
        credentials=(connections.get("notion", {"type": "user"}),),
        load=load,
    )
    middleware = create_credential_gate_middleware([consumer])
    assert middleware is not None

    middleware.before_agent(None, {})
    assert len(loads) == 1
    clock.now = gate_mod._TOOL_CACHE_TTL_SECONDS * 2

    def refresh() -> None:
        started.wait()
        middleware.wrap_model_call(_ModelRequest([]), lambda seen: seen)

    threads = [threading.Thread(target=refresh) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(loads) == 2


def test_a_failed_load_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    attempts: list[int] = []

    def load(_access_tokens: Mapping[str, str], _context: AgentAuthContext) -> list[object]:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("mcp unreachable")
        return [_Tool("notion__search")]

    middleware = create_credential_gate_middleware(
        [
            CredentialConsumer(
                declaration=DECLARATION,
                credentials=(connections.get("notion", {"type": "user"}),),
                load=load,
            )
        ]
    )
    assert middleware is not None

    with pytest.raises(RuntimeError, match="mcp unreachable"):
        middleware.before_agent(None, {})
    middleware.before_agent(None, {})

    assert len(attempts) == 2


def test_async_hooks_gate_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
    agent_auth: _FakeAgentAuth,
    interrupts: list[object],
) -> None:
    _as_caller(monkeypatch, "principal-1")
    agent_auth.workspace = _oauth_workspace("notion")
    agent_auth.granted["notion"] = "ya29.caller"
    middleware = create_credential_gate_middleware([_consumer()])
    assert middleware is not None

    async def _drive() -> _ModelRequest:
        await middleware.abefore_agent(None, {})

        async def handler(seen: _ModelRequest) -> _ModelRequest:
            return seen

        return await middleware.awrap_model_call(_ModelRequest(), handler)

    request = asyncio.run(_drive())

    assert [tool.name for tool in request.tools] == ["notion__search"]
