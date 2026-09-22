"""Execute the agent's tool node without invoking its model."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import HTTPException
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph_sdk import get_client
from pydantic import BaseModel, JsonValue, TypeAdapter

from agent.middleware.dynamic_tools import DynamicToolMiddleware
from agent.run_config import RunConfig
from agent.sandboxes.tool_data import ToolContext, load_context, save_context
from agent.sandboxes.tool_models import ToolDescription
from agent.sandboxes.tool_store import ToolStore

_json = TypeAdapter(JsonValue)
EXCLUDED_TOOLS = frozenset({"enter_plan_mode", "approve_plan", "load_integration_tools"})


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


@dataclass
class ToolSurface:
    graph: CompiledStateGraph | None = None
    dynamic: DynamicToolMiddleware | None = None
    excluded: frozenset[str] = frozenset()
    plan_excluded: frozenset[str] = frozenset()
    tools: dict[str, BaseTool] = field(default_factory=dict)
    integration_names: list[str] = field(default_factory=list)

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
        if state.get("plan_mode") is True:
            excluded = excluded | self.plan_excluded
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
        builder = StateGraph(self.graph.builder.state_schema)
        builder.add_node("tools", node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        executor = builder.compile(store=ToolStore())
        call_id = f"sandbox-{uuid4().hex}"
        call = AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": call_id}])
        messages = state.get("messages", [])
        loaded = state.get("loaded_integration_tools", [])
        integration_names = list(loaded) if isinstance(loaded, list) else []
        if name in self.integration_names and name not in integration_names:
            integration_names.append(name)
        state = {
            **state,
            "messages": [*(messages if isinstance(messages, list) else []), call],
            "loaded_integration_tools": integration_names,
        }
        update: dict[str, object] = {}
        async for chunk in executor.astream(state, config, stream_mode="updates"):
            values = chunk.get("tools")
            if isinstance(values, dict):
                update.update(values)
        results = update.get("messages", [])
        result = (
            next(
                (
                    message
                    for message in results
                    if isinstance(message, ToolMessage) and message.tool_call_id == call_id
                ),
                None,
            )
            if isinstance(results, list)
            else None
        )
        if result is None or not isinstance(results, list):
            raise RuntimeError("Tool did not return a result")
        return {"status": result.status, "content": _json.validate_python(result.content)}


async def load_tool_surface(
    thread_id: str,
) -> tuple[ToolSurface, RunnableConfig, dict[str, object]]:
    from agent.server import build_agent

    context = await load_context(thread_id)
    if context is None:
        raise HTTPException(409, "Thread tools have not been initialized")
    snapshot = await get_client().threads.get_state(thread_id)
    values = snapshot.get("values")
    state: dict[str, object] = dict(values) if isinstance(values, dict) else {}
    state.setdefault("plan_mode", context.configurable.get("plan_mode") is True)
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
