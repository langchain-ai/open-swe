"""Execute the agent's tool node without invoking its model."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

from fastapi import HTTPException
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.typing import StateT
from langgraph_sdk import get_client
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe.middleware.dynamic_tools import DynamicToolMiddleware
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_data import ToolContext, load_context, save_context
from openswe.sandboxes.tool_models import ToolDescription

_json = TypeAdapter(JsonValue)
# Task workers share this channel with their coordinator, so task coordination stays model-only.
EXCLUDED_TOOLS = frozenset(
    {"load_integration_tools", "spawn_worker", "control_worker", "message_task_thread"}
)


async def save_tool_context(thread_id: str, config: RunnableConfig) -> None:
    cfg = RunConfig.from_config(config)
    values = {
        name: _json.validate_python(value)
        for name, value in cfg.dump().items()
        if name in RunConfig.model_fields and name not in {"run_id", "invocation_id"}
    }
    await save_context(thread_id, ToolContext(configurable=values))


def tool_parameters(tool: BaseTool) -> dict[str, JsonValue]:
    schema = tool.tool_call_schema
    if isinstance(schema, dict):
        return schema
    if issubclass(schema, BaseModel):
        return schema.model_json_schema()
    return schema.schema()


# The file and shell tools deepagents' FilesystemMiddleware gives every deep agent.
FILESYSTEM_TOOL_NAMES: tuple[str, ...] = (
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
)


@dataclass(frozen=True)
class ModelChoice:
    model_id: str
    kwargs: Mapping[str, object]


@dataclass(frozen=True)
class SurfaceModels:
    """The models a graph factory picked, for a runtime that builds its own."""

    main: ModelChoice
    subagent: ModelChoice
    use_gateway: bool
    # Model routes the agent's model selection picks between, by route name.
    routes: Mapping[str, ModelChoice] = field(default_factory=dict)
    fallback: ModelChoice | None = None


@dataclass(frozen=True)
class SurfaceSubagent:
    name: str
    description: str
    system_prompt: str
    # None gives the subagent every tool its parent has.
    tools: tuple[str, ...] | None = None


@dataclass
class ToolSurface:
    graph: CompiledStateGraph | None = None
    dynamic: DynamicToolMiddleware | None = None
    excluded: frozenset[str] = frozenset()
    tools: dict[str, BaseTool] = field(default_factory=dict)
    integration_names: list[str] = field(default_factory=list)
    # What a model loop running elsewhere needs from the graph: its own middleware,
    # in order, and the models and subagents the factory picked.
    middleware: Sequence[AgentMiddleware] = ()
    models: SurfaceModels | None = None
    subagents: Sequence[SurfaceSubagent] = ()
    # Whether the graph's core tools it seldom needs belong in a runtime's code mode.
    code_mode: bool = False

    async def prepare(self, state: Mapping[str, object]) -> None:
        if self.graph is None:
            raise RuntimeError("Tool graph was not initialized")
        node = self.graph.builder.nodes["tools"].runnable
        if not isinstance(node, ToolNode):
            raise RuntimeError("Agent tools node is unavailable")
        tools = dict(node.tools_by_name)
        if self.dynamic:
            integrations = await self.dynamic.catalog_tools()
            self.integration_names = [tool.name for tool in integrations]
            tools.update({tool.name: tool for tool in integrations})
        excluded = self.excluded | EXCLUDED_TOOLS
        self.tools = {name: tool for name, tool in tools.items() if name not in excluded}

    def catalog(self, query: str = "") -> list[ToolDescription]:
        terms = query.casefold().split()
        results: list[ToolDescription] = []
        for name, tool in sorted(self.tools.items()):
            if not all(term in f"{name} {tool.description}".casefold() for term in terms):
                continue
            parameters = tool_parameters(tool)
            results.append(
                ToolDescription(name=name, description=tool.description, parameters=parameters)
            )
        return results

    async def invoke(
        self,
        thread_id: str,
        config: RunnableConfig,
        state: dict[str, object],
        name: str,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        result = await self.run(config, state, name, arguments)
        return {"status": result.message.status, "content": result.content}

    async def run(
        self,
        config: RunnableConfig,
        state: Mapping[str, object],
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        call_id: str | None = None,
    ) -> ToolNodeResult:
        """Run one call through the graph's own tools node, keeping its state updates."""
        tool = self.tools.get(name)
        if tool is None:
            raise HTTPException(404, "Tool is unavailable")
        parameters = tool_parameters(tool)
        properties = parameters.get("properties", {})
        if not isinstance(properties, dict) or set(arguments) - properties.keys():
            raise HTTPException(422, "Unexpected tool arguments")
        if self.graph is None:
            raise RuntimeError("Tool graph was not initialized")
        node = self.graph.builder.nodes["tools"].runnable
        if not isinstance(node, ToolNode):
            raise RuntimeError("Agent tools node is unavailable")
        loaded = state.get("loaded_integration_tools", [])
        integration_names = list(loaded) if isinstance(loaded, list) else []
        if name in self.integration_names and name not in integration_names:
            integration_names.append(name)
        return await run_tool_node(
            node,
            self.graph.builder.state_schema,
            {**state, "loaded_integration_tools": integration_names},
            config,
            name,
            arguments,
            call_id=call_id,
        )


@dataclass(frozen=True)
class ToolNodeResult:
    """One tool call's outcome: its result message and every other state update it made."""

    message: ToolMessage
    # Other messages the call added, such as a Command's extra messages.
    messages: Sequence[AnyMessage]
    update: Mapping[str, object]
    # What the call streamed through ``get_stream_writer``.
    events: Sequence[object]

    @property
    def content(self) -> JsonValue:
        return _json.validate_python(self.message.content)


def _merge_update(update: dict[str, object], values: object) -> None:
    for value in values if isinstance(values, list) else [values]:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if key == "messages":
                existing = update.get("messages")
                prior = existing if isinstance(existing, list) else []
                update["messages"] = [*prior, *(item if isinstance(item, list) else [item])]
            else:
                update[key] = item


async def run_tool_node(
    node: ToolNode,
    state_schema: type[StateT],
    state: Mapping[str, object],
    config: RunnableConfig,
    name: str,
    arguments: Mapping[str, JsonValue],
    *,
    call_id: str | None = None,
) -> ToolNodeResult:
    """Run one tool call through ``node`` as the agent's own tools node would.

    Injected state and the runnable config resolve exactly as in a graph
    run, and the node's tool-call middleware wraps the call.
    """
    builder = StateGraph(state_schema)
    builder.add_node("tools", node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    executor = builder.compile()
    call_id = call_id or f"sandbox-{uuid4().hex}"
    call = AIMessage(
        content="", tool_calls=[{"name": name, "args": dict(arguments), "id": call_id}]
    )
    messages = state.get("messages", [])
    input_state = {
        **state,
        "messages": [*(messages if isinstance(messages, list) else []), call],
    }
    update: dict[str, object] = {}
    events: list[object] = []
    async for mode, chunk in executor.astream(
        cast(StateT, input_state), config, stream_mode=["updates", "custom"]
    ):
        if mode == "custom":
            events.append(chunk)
        elif isinstance(chunk, dict):
            _merge_update(update, chunk.get("tools"))
    results = update.pop("messages", [])
    added = results if isinstance(results, list) else []
    result = next(
        (
            message
            for message in added
            if isinstance(message, ToolMessage) and message.tool_call_id == call_id
        ),
        None,
    )
    if result is None:
        raise RuntimeError("Tool did not return a result")
    return ToolNodeResult(
        message=result,
        messages=[message for message in added if message is not result],
        update=update,
        events=events,
    )


async def invoke_tool_node(
    node: ToolNode,
    state_schema: type[StateT],
    state: Mapping[str, object],
    config: RunnableConfig,
    name: str,
    arguments: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Run one tool call through ``node`` and return its status and content."""
    result = await run_tool_node(node, state_schema, state, config, name, arguments)
    return {"status": result.message.status, "content": result.content}


async def load_tool_surface(
    thread_id: str,
) -> tuple[ToolSurface, RunnableConfig, dict[str, object]]:
    from openswe.server import build_agent

    context = await load_context(thread_id)
    if context is None:
        raise HTTPException(409, "Thread tools have not been initialized")
    snapshot = await get_client().threads.get_state(thread_id)
    values = snapshot.get("values")
    state: dict[str, object] = dict(values) if isinstance(values, dict) else {}
    config: RunnableConfig = {
        "configurable": {
            **context.configurable,
            "thread_id": thread_id,
            "__is_for_execution__": True,
        },
    }
    surface = ToolSurface()
    await build_agent(config, tool_surface=surface)
    await surface.prepare(state)
    return surface, config, state
