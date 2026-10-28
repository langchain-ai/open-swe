"""Credential gate topology during native Agent Server inspection."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.store.memory import InMemoryStore
from langgraph_sdk.runtime import ServerRuntime, _ExecutionRuntime, _ReadRuntime

from managed_deepagents import (
    DeepAgentDefinition,
    ManagedRunContext,
    connections,
    define_deep_agent,
    define_mcp,
    define_sandbox,
    runtime,
)
from managed_deepagents import _credential_gate as gate_mod
from managed_deepagents import _sandbox_proxy as sandbox_proxy


class _Model(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "credential-gate-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


@pytest.mark.parametrize("consumer", ["sandbox", "mcp"])
@pytest.mark.parametrize("access_context", ["threads.read", "threads.update"])
def test_factory_inspection_keeps_credential_gate_without_loading_resources(
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
    access_context: Literal["threads.read", "threads.update"],
) -> None:
    connection = connections.get("example", {"type": "user"})
    model = _Model()

    def build(_runtime: ServerRuntime[ManagedRunContext]) -> DeepAgentDefinition:
        if consumer == "sandbox":
            return define_deep_agent(
                name="credential-inspection",
                model=model,
                sandbox=define_sandbox(
                    proxy_config={
                        "rules": [{"headers": [{"name": "Authorization", "value": connection}]}]
                    }
                ),
            )
        return define_deep_agent(
            name="credential-inspection",
            model=model,
            mcp=[
                define_mcp(
                    servers={
                        "example": {
                            "transport": "http",
                            "url": "https://example.com/mcp",
                            "connection": connection,
                        }
                    }
                )
            ],
        )

    def forbidden(*args: object, **kwargs: object) -> object:
        pytest.fail("inspection must not load resources or credentials")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_resolve_managed_sandbox", forbidden)
        patch.setattr(runtime, "_create_langsmith_sandbox", forbidden)
        patch.setattr(sandbox_proxy, "resolve_sandbox_proxy_config", forbidden)
        patch.setattr(runtime, "_open_context_backend", forbidden)
        patch.setattr(runtime, "load_connector_tools", forbidden)
        patch.setattr(gate_mod, "AgentAuth", forbidden)
        patch.setattr(connections, "resolve", forbidden)
        inspected = asyncio.run(
            runtime.compile_managed_agent_async(
                build,
                report_telemetry=False,
                runtime=_ReadRuntime(access_context=access_context, store=InMemoryStore()),
            )
        )

    reference = asyncio.run(
        runtime.compile_managed_agent_async(
            build,
            report_telemetry=False,
            runtime=_ExecutionRuntime(
                access_context="threads.create_run", store=InMemoryStore(), context={}
            ),
        )
    )
    assert "mda-credential-gate.before_agent" in reference.get_graph().nodes
    assert list(inspected.get_graph().nodes) == list(reference.get_graph().nodes)
    assert inspected.get_graph().edges == reference.get_graph().edges
    assert inspected.get_input_jsonschema() == reference.get_input_jsonschema()
    assert inspected.get_output_jsonschema() == reference.get_output_jsonschema()
