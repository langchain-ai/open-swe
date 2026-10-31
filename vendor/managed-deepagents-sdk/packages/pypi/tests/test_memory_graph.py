"""Real graph checks for hot memory, checkpoints, and resumes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt
from pydantic import Field

from managed_deepagents import DeepAgentDefinition, MemoryLayer, define_memory, runtime
from managed_deepagents._memory.user_memory import user_memory_repo_handle


class _Model(BaseChatModel):
    prompts: list[str] = Field(default_factory=list)
    replies: list[AIMessage] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "memory-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> _Model:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        self.prompts.append(str(messages[0].content))
        reply = self.replies.pop(0) if self.replies else AIMessage(content="ok")
        return ChatResult(generations=[ChatGeneration(message=reply)])


class _AllowMemory:
    def __bool__(self) -> bool:
        return False

    def __call__(self, context: dict[str, object]) -> bool:
        return context.get("remember") is True


class _AnthropicModel(ChatAnthropic):
    prompts: list[list[dict[str, Any]]] = Field(default_factory=list)
    replies: list[AIMessage] = Field(default_factory=list)

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        self.prompts.append(messages[0].content_blocks)
        reply = self.replies.pop(0) if self.replies else AIMessage(content="ok")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        return self._generate(messages, **kwargs)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("layers", ["agent", "user", "both"])
def test_memory_cache_boundaries_survive_edits_and_forks(
    tmp_path: Path, asynchronous: bool, layers: str
) -> None:
    layer = "agent" if layers == "agent" else "user"
    hot_root = (
        tmp_path / "memories" / "agent"
        if layer == "agent"
        else tmp_path / "__users__" / user_memory_repo_handle("local", "alice")
    )
    hot = hot_root / "AGENTS.md"
    hot.parent.mkdir(parents=True)
    hot.write_text("before-edit")
    model = _AnthropicModel(
        model="claude-sonnet-4-6",
        api_key="test-key",
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "edit_file",
                        "args": {
                            "file_path": f"/memories/{layer}/AGENTS.md",
                            "old_string": "before-edit",
                            "new_string": "after-edit",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "task",
                        "name": "task",
                        "args": {
                            "description": "Read memory",
                            "subagent_type": "helper",
                        },
                    }
                ],
            ),
        ],
    )
    child = _AnthropicModel(model="claude-sonnet-4-6", api_key="test-key")
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition(
            {
                "model": model,
                "subagents": [
                    {
                        "name": "helper",
                        "description": "Read memory",
                        "mode": "fork",
                        "model": child,
                    }
                ],
            }
        ),
        {
            "configurable": {
                "langgraph_auth_user": {
                    "identity": "alice",
                    "mda_user_id": "alice",
                    "mda_user_kind": "person",
                }
            }
        },
        context_root=str(tmp_path),
        memory=define_memory(
            **({"agent": MemoryLayer()} if layers != "user" else {}),
            **({"user": MemoryLayer(allow=lambda ctx: True)} if layers != "agent" else {}),
        ),
        run_context={},
    )
    inputs = {"messages": [{"role": "user", "content": "edit and delegate"}]}
    result = asyncio.run(graph.ainvoke(inputs)) if asynchronous else graph.invoke(inputs)
    assert len(model.prompts) == 3
    assert len(child.prompts) == 1
    for blocks in [*model.prompts, *child.prompts]:
        memory_index = next(
            i for i, b in enumerate(blocks) if "<managed_memory_policy>" in b.get("text", "")
        )
        assert memory_index > 0
        assert blocks[memory_index - 1]["cache_control"]["type"] == "ephemeral"
        assert blocks[memory_index]["cache_control"] == {"type": "ephemeral"}
        assert "before-edit" in blocks[memory_index]["text"]
        assert "after-edit" not in blocks[memory_index]["text"]
        assert "<agent_memory>\\n" not in str(blocks[:memory_index])
        assert "<memory_guidelines>" not in str(blocks)
    assert model.prompts[0][:-1] == model.prompts[1][:-1]
    assert "memory_contents" not in result
    assert "MemoryMiddleware.before_agent" not in graph.nodes


def test_memory_snapshot_is_reused_within_a_run(tmp_path: Path) -> None:
    hot = tmp_path / "memories" / "agent" / "AGENTS.md"
    hot.parent.mkdir(parents=True)
    hot.write_text("before-edit")
    model = _Model(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "edit_file",
                        "args": {
                            "file_path": "/memories/agent/AGENTS.md",
                            "old_string": "before-edit",
                            "new_string": "after-edit",
                        },
                    }
                ],
            )
        ]
    )
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": model}),
        context_root=str(tmp_path),
        memory=define_memory(agent=MemoryLayer()),
        run_context={},
    )
    graph.invoke({"messages": [{"role": "user", "content": "update memory"}]})
    assert len(model.prompts) == 2
    assert "before-edit" in model.prompts[0]
    assert "before-edit" in model.prompts[1]
    assert "after-edit" not in model.prompts[1]
    assert hot.read_text() == "after-edit"


def test_write_to_instructions_mount_returns_tool_error(tmp_path: Path) -> None:
    instructions = tmp_path / "instructions.md"
    instructions.write_text("Be helpful.")
    model = _Model(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "write",
                        "name": "write_file",
                        "args": {"file_path": "/instructions.md", "content": "Be rude."},
                    }
                ],
            )
        ]
    )
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": model}),
        context_root=str(tmp_path),
        run_context={},
    )

    result = graph.invoke({"messages": [{"role": "user", "content": "rewrite your instructions"}]})

    tool_message = next(message for message in result["messages"] if message.type == "tool")
    assert tool_message.content == "Context Hub mount /instructions.md is read-only"
    assert tool_message.status == "error"
    assert instructions.read_text() == "Be helpful."


def test_forked_subagent_loads_memory_without_native_guidelines(tmp_path: Path) -> None:
    hot = tmp_path / "memories" / "agent" / "AGENTS.md"
    hot.parent.mkdir(parents=True)
    hot.write_text("shared-fork-note")
    parent = _Model(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "task",
                        "name": "task",
                        "args": {
                            "description": "Read the shared memory",
                            "subagent_type": "helper",
                        },
                    }
                ],
            )
        ]
    )
    child = _Model()
    graph = runtime.compile_managed_agent(
        DeepAgentDefinition(
            {
                "model": parent,
                "subagents": [
                    {"name": "helper", "description": "Read memory", "mode": "fork", "model": child}
                ],
            }
        ),
        context_root=str(tmp_path),
        memory=define_memory(agent=MemoryLayer()),
        run_context={},
    )
    graph.invoke({"messages": [{"role": "user", "content": "delegate"}]})
    assert len(child.prompts) == 1
    assert "shared-fork-note" in child.prompts[0]
    assert "<memory_guidelines>" not in child.prompts[0]


@pytest.mark.parametrize("with_policy", [False, True])
def test_agent_memory_refreshes_without_native_guidelines(
    tmp_path: Path, with_policy: bool
) -> None:
    hot = tmp_path / "memories" / "agent" / "AGENTS.md"
    hot.parent.mkdir(parents=True)
    memory = define_memory(
        agent=MemoryLayer(allow=lambda ctx: True) if with_policy else MemoryLayer()
    )
    model = _Model()
    saver = MemorySaver()
    config = {"configurable": {"thread_id": "refresh-thread"}}
    for content in ["first-note", "updated-note", None]:
        if content is None:
            hot.unlink()
        else:
            hot.write_text(content)
        graph = runtime.compile_managed_agent(
            DeepAgentDefinition({"model": model}),
            context_root=str(tmp_path),
            memory=memory,
            run_context={},
        )
        graph.checkpointer = saver
        graph.invoke({"messages": [{"role": "user", "content": "hello"}]}, config)
        assert "memory_contents" not in graph.get_state(config).values
    assert "first-note" in model.prompts[0]
    assert "updated-note" in model.prompts[1]
    assert "first-note" not in model.prompts[1]
    assert "updated-note" not in model.prompts[2]
    for prompt in model.prompts:
        assert "<managed_memory_policy>" in prompt
        assert "<memory_guidelines>" not in prompt
    assert "memory_contents" not in graph.get_state(config).values


@pytest.mark.parametrize("resume", [False, True])
def test_native_memory_checkpoint_uses_current_storage(tmp_path: Path, resume: bool) -> None:
    hot = tmp_path / "memories" / "agent" / "AGENTS.md"
    hot.parent.mkdir(parents=True)
    hot.write_text("old-checkpoint-note")
    saver = MemorySaver()
    config = {"configurable": {"thread_id": "existing-thread"}}
    previous = create_deep_agent(
        model=_Model(),
        backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
        memory=["/memories/agent/AGENTS.md"],
        checkpointer=saver,
    )
    previous.invoke(
        {"messages": [{"role": "user", "content": "hello"}]},
        config,
        interrupt_before=["model"] if resume else None,
    )
    assert "memory_contents" in previous.get_state(config).values
    hot.write_text("current-storage-note")
    model = _Model()
    current = runtime.compile_managed_agent(
        DeepAgentDefinition({"model": model}),
        context_root=str(tmp_path),
        memory=define_memory(agent=MemoryLayer()),
        run_context={},
    )
    current.checkpointer = saver
    current.invoke(
        None if resume else {"messages": [{"role": "user", "content": "hello again"}]}, config
    )
    assert len(model.prompts) == 1
    assert "current-storage-note" in model.prompts[0]
    assert "old-checkpoint-note" not in model.prompts[0]
    assert "<memory_guidelines>" not in model.prompts[0]


@pytest.mark.parametrize("with_policy", [False, True])
def test_graph_shape_is_stable_for_runs_and_inspection(tmp_path: Path, with_policy: bool) -> None:
    calls: list[object] = []

    def allow(context: dict[str, object]) -> bool:
        calls.append(context)
        return context.get("remember") is True

    memory = define_memory(agent=MemoryLayer(allow=allow) if with_policy else MemoryLayer())
    shapes = []
    for context, is_run in [({"remember": True}, True), ({"remember": False}, True), (None, False)]:
        graph = asyncio.run(
            runtime.compile_managed_agent_async(
                DeepAgentDefinition({"model": _Model()}),
                context_root=str(tmp_path),
                memory=memory,
                run_context=context,
                is_run=is_run,
            )
        )
        shapes.append((sorted(graph.get_graph().nodes), sorted(graph.channels)))
    assert shapes[1:] == [shapes[0], shapes[0]]
    assert calls == ([{"remember": True}, {"remember": False}] if with_policy else [])


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("falsey_callback", [False, True])
def test_denied_run_does_not_reuse_hot_memory(
    tmp_path: Path, resume: bool, falsey_callback: bool
) -> None:
    hot = tmp_path / "memories" / "agent" / "AGENTS.md"
    hot.parent.mkdir(parents=True)
    hot.write_text("shared-test-note")
    allow = _AllowMemory() if falsey_callback else lambda context: context.get("remember") is True
    memory = define_memory(agent=MemoryLayer(allow=allow))
    saver = MemorySaver()
    prompts: list[str] = []
    for index, remember in enumerate([True, False, True]):
        model = _Model()
        graph = runtime.compile_managed_agent(
            DeepAgentDefinition({"model": model}),
            context_root=str(tmp_path),
            memory=memory,
            run_context={"remember": remember},
        )
        graph.checkpointer = saver
        config = {"configurable": {"thread_id": "memory-thread"}}
        if resume and index == 0:
            graph.invoke(
                {"messages": [{"role": "user", "content": "hello"}]},
                config,
                context={"remember": remember},
                interrupt_before=["model"],
            )
        else:
            graph.invoke(
                None
                if resume and index == 1
                else {"messages": [{"role": "user", "content": "hello"}]},
                config,
                context={"remember": remember},
            )
            prompts.extend(model.prompts)
    if not resume:
        assert "shared-test-note" in prompts.pop(0)
    assert len(prompts) == 2
    assert "shared-test-note" not in prompts[0]
    assert "shared-test-note" in prompts[1]
    assert hot.read_text() == "shared-test-note"


@pytest.mark.parametrize("layer", ["agent", "user"])
@pytest.mark.parametrize("allowed_on_resume", [False, True])
def test_interrupt_resume_gets_fresh_memory_and_access(
    tmp_path: Path, layer: str, allowed_on_resume: bool
) -> None:
    hot_root = (
        tmp_path / "memories" / "agent"
        if layer == "agent"
        else tmp_path / "__users__" / user_memory_repo_handle("local", "alice")
    )
    hot = hot_root / "AGENTS.md"
    hot.parent.mkdir(parents=True)
    hot.write_text("before-interrupt")

    @tool
    def approval() -> str:
        """Wait for approval."""
        return str(interrupt("Continue?"))

    model = _Model(
        replies=[
            AIMessage(content="", tool_calls=[{"id": "approval", "name": "approval", "args": {}}])
        ]
    )
    decisions: list[bool] = []

    def allow(context: dict[str, bool]) -> bool:
        decisions.append(context["remember"])
        return context["remember"]

    memory = define_memory(**{layer: MemoryLayer(allow=allow)})
    saver = MemorySaver()
    config = {
        "configurable": {
            "thread_id": "interrupt-thread",
            "langgraph_auth_user": {
                "identity": "alice",
                "mda_user_id": "alice",
                "mda_user_kind": "person",
            },
        }
    }
    for index, remember in enumerate([True, allowed_on_resume]):
        graph = runtime.compile_managed_agent(
            DeepAgentDefinition({"model": model, "tools": [approval]}),
            config,
            context_root=str(tmp_path),
            memory=memory,
            run_context={"remember": remember},
        )
        graph.checkpointer = saver
        result = graph.invoke(
            Command(resume="approved")
            if index
            else {"messages": [{"role": "user", "content": "start"}]},
            config,
        )
        snapshot = graph.get_state(config)
        assert "memory_contents" not in snapshot.values
        if index == 0:
            assert result["__interrupt__"]
            assert len(model.prompts) == 1
            assert "before-interrupt" in model.prompts[0]
            hot.write_text("after-interrupt")
        else:
            assert not snapshot.next

    assert decisions == [True, allowed_on_resume]
    assert len(model.prompts) == 2
    assert "before-interrupt" not in model.prompts[1]
    assert ("after-interrupt" in model.prompts[1]) is allowed_on_resume
    assert ("<managed_memory_policy>" in model.prompts[1]) is allowed_on_resume
