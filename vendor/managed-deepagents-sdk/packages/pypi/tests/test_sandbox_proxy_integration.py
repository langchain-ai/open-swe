"""Exercise proxy credentials through the compiled agent and its execute tool."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import ExecuteResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from managed_deepagents import _credential_gate, _sandbox_proxy, bearer, define_sandbox, runtime
from managed_deepagents._agent_auth import AgentAuth
from managed_deepagents._connections import (
    AgentAuthConnectionStore,
    ConnectionHttpResponse,
    _Connections,
)
from managed_deepagents.define_deep_agent import DeepAgentDefinition


class _AgentAuthServer:
    """Serve the real Agent Auth client with a mutable user grant."""

    def __init__(self) -> None:
        self.token: str | None = "original-token"

    def request(
        self, method: str, path: str, *, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object]]:
        if "/connections?" in path:
            row = {"id": "conn-github", "slug": "github", "kind": "oauth2", "owner_policy": "user"}
            return 200, {"items": [row]}
        if "/connections/" in path:
            credential = {"kind": "oauth2"}
            if self.token is not None:
                credential["credential_id"] = "credential-github"
            return 200, {"id": "conn-github", "credential": credential}
        if path.endswith("/credentials/credential-github/oauth2"):
            return 200, {"access_token": self.token}
        if path.endswith("/oauth-authorization-sessions"):
            return 200, {
                "id": "session-github",
                "verification_url": "https://idp.example/github",
            }
        raise AssertionError(f"Unexpected Agent Auth request: {method} {path}")

    def http_get(self, url: str, headers: object, timeout: float) -> ConnectionHttpResponse:
        status, body = self.request("GET", url)
        return {"status": status, "body": json.dumps(body).encode()}


class _ExecuteModel(BaseChatModel):
    calls: int = 0
    before_execute: Callable[[], None] | None = None

    @property
    def _llm_type(self) -> str:
        return "sandbox-proxy-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ExecuteModel:
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.calls += 1
        if self.calls > 2:
            message = AIMessage(content="Command complete.")
        else:
            if self.before_execute is not None:
                self.before_execute()
            message = AIMessage(
                content="",
                tool_calls=[
                    {"id": f"execute-{self.calls}", "name": "execute", "args": {"command": "pwd"}}
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    _credential_gate.clear_credential_tool_cache_for_tests()
    env = {
        "LANGSMITH_API_KEY": "test-key",
        "LANGSMITH_HOST_PROJECT_ID": "deployment-1",
        "LANGSMITH_WORKSPACE_ID": "workspace-1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    server = _AgentAuthServer()
    monkeypatch.setattr(
        _credential_gate, "AgentAuth", lambda context: AgentAuth(context, request=server.request)
    )
    store = AgentAuthConnectionStore(env=env, http_get=server.http_get)
    lookup = AsyncMock(wraps=store.fetch)
    monkeypatch.setattr(store, "fetch", lookup)
    accessor = _Connections(store)
    ref = accessor.get("github", {"type": "user"})
    header = {"name": "Authorization", "value": bearer(ref)}
    sandbox = define_sandbox(proxy_config={"rules": [{"headers": [header]}]})
    backend = MagicMock()
    backend.id = "box-1"
    backend.execute.return_value = ExecuteResponse(output="/workspace", exit_code=0)

    backend.aexecute = AsyncMock(return_value=backend.execute.return_value)
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", lambda *args, **kwargs: backend)
    monkeypatch.setattr(runtime, "_peek_managed_host_name", lambda key: "box-1")
    patch = MagicMock()
    monkeypatch.setattr(_sandbox_proxy, "patch_sandbox_proxy_config", patch)
    model = _ExecuteModel()
    definition = DeepAgentDefinition({"model": model, "checkpointer": InMemorySaver()})

    def invoke(value: object, asynchronous: bool, user: str = "alice") -> dict[str, Any]:
        principal = {
            "identity": user,
            "mda_user_id": user,
            "mda_user_kind": "person",
            "mda_agent_auth_principal_id": user,
        }
        config = {"configurable": {"thread_id": "shared", "langgraph_auth_user": principal}}
        # MDA calls its graph factory with the current config on every run and resume.
        graph = runtime.compile_managed_agent(definition, config, sandbox=sandbox)
        return (
            asyncio.run(graph.ainvoke(value, config))
            if asynchronous
            else graph.invoke(value, config)
        )

    yield SimpleNamespace(
        invoke=invoke, model=model, server=server, box=backend, patch=patch, lookup=lookup
    )
    _credential_gate.clear_credential_tool_cache_for_tests()


_INPUT = {"messages": [{"role": "user", "content": "Run pwd."}]}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_gate_precedes_model_and_revoked_grant_interrupts_execution_until_resume(
    agent: SimpleNamespace,
    asynchronous: bool,
) -> None:
    agent.server.token = None
    result = agent.invoke(_INPUT, asynchronous)
    assert result["__interrupt__"][0].value["type"] == "credential_authorization_required"
    assert agent.model.calls == 0
    agent.box.execute.assert_not_called()
    agent.box.aexecute.assert_not_called()
    agent.patch.assert_not_called()
    agent.server.token = "original-token"

    def revoke_second_command() -> None:
        if agent.model.calls == 2:
            agent.server.token = None

    agent.model.before_execute = revoke_second_command
    result = agent.invoke(Command(resume=True), asynchronous)
    assert (
        result["__interrupt__"][0].value["credentials"][0]["connect_url"]
        == "https://idp.example/github"
    )
    assert agent.model.calls == 2
    command = agent.box.aexecute if asynchronous else agent.box.execute
    command.assert_called_once_with("pwd")
    assert agent.patch.call_count == 1
    agent.server.token = "original-token"  # An unchanged credential still needs PATCH on resume.
    result = agent.invoke(Command(resume=True), asynchronous)
    assert not result.get("__interrupt__")
    assert command.call_count == 2
    assert agent.patch.call_count == 2
    assert result["messages"][-2].name == "execute"
    assert result["messages"][-2].status == "success"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_compiled_commands_resolve_each_time_skip_unchanged_patch_and_refresh_new_runs(
    agent: SimpleNamespace,
    asynchronous: bool,
) -> None:
    for user in ["alice", "bob", "alice"]:
        agent.model.calls = 0
        agent.server.token = user
        agent.invoke(_INPUT, asynchronous, user)
    assert agent.box.execute.call_count + agent.box.aexecute.call_count == 6
    assert [
        call.args[2]["rules"][0]["headers"][0]["value"] for call in agent.patch.call_args_list
    ] == [
        "Bearer alice",
        "Bearer bob",
        "Bearer alice",
    ]
    assert [call.args[0]["identity"]["user"]["id"] for call in agent.lookup.call_args_list] == [
        "alice",
        "alice",
        "bob",
        "bob",
        "alice",
        "alice",
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_patch_failure_stops_compiled_execution(agent: SimpleNamespace, asynchronous: bool) -> None:
    agent.patch.side_effect = RuntimeError("HTTP 502 original-token")
    with pytest.raises(RuntimeError, match="Could not update sandbox proxy credentials") as error:
        agent.invoke(_INPUT, asynchronous)
    assert "original-token" not in str(error.value)
    assert agent.model.calls == 1
    agent.patch.assert_called_once()
    agent.box.execute.assert_not_called()
    agent.box.aexecute.assert_not_called()
