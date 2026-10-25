"""What a graph factory plans for a run: its tools, middleware, models and subagents.

The model loop runs on Managed Deep Agents, which builds a tool for each of the
planned tools and calls the backend to run it (``openswe.agent_api``).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_core.tools import tool as as_tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.typing import StateT
from pydantic import BaseModel, JsonValue, TypeAdapter
from runtime_api.catalog import FileCheck, InboxSpec, LoopRules

from openswe.mcp.models import MCPToolProvenance
from openswe.middleware.dynamic_tools import DynamicToolMiddleware
from openswe.prompts import prompt
from openswe.sandboxes.tool_models import ToolDescription

_json = TypeAdapter(JsonValue)
# Every surface lists the integration tools themselves, so none offers their loader.
EXCLUDED_TOOLS = frozenset({"load_integration_tools"})


def tool_parameters(tool: BaseTool) -> dict[str, JsonValue]:
    schema = tool.tool_call_schema
    if isinstance(schema, dict):
        return schema
    if issubclass(schema, BaseModel):
        return schema.model_json_schema()
    return schema.schema()


def describe_tool(name: str, tool: BaseTool) -> ToolDescription:
    provenance = (tool.metadata or {}).get("mcp_provenance")
    if not isinstance(provenance, MCPToolProvenance):
        return ToolDescription(
            name=name, description=tool.description, parameters=tool_parameters(tool)
        )
    return ToolDescription(
        name=name,
        description=tool.description
        + "\n\n"
        + prompt("tools/mcp-provenance", provenance=provenance.model_dump_json()),
        parameters=tool_parameters(tool),
        mcp_provenance=provenance,
    )


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
    # What a text-only model's call goes to once the conversation holds an image.
    vision: ModelChoice | None = None


@dataclass(frozen=True)
class SurfaceSubagent:
    name: str
    description: str
    system_prompt: str
    # None gives the subagent every tool its parent has.
    tools: tuple[str, ...] | None = None


type PlannedTool = BaseTool | Callable[..., object] | dict[str, object]


@dataclass
class ToolSurface:
    """A run as its graph factory plans it."""

    # The factory's own tools; its middleware adds theirs.
    base_tools: Sequence[PlannedTool] = ()
    dynamic: DynamicToolMiddleware | None = None
    excluded: frozenset[str] = frozenset()
    tools: dict[str, BaseTool] = field(default_factory=dict)
    integration_names: list[str] = field(default_factory=list)
    # What a model loop running elsewhere needs from the graph: its own middleware,
    # in order, and the models and subagents the factory picked.
    middleware: Sequence[AgentMiddleware] = ()
    models: SurfaceModels | None = None
    subagents: Sequence[SurfaceSubagent] = ()
    # The thread's files: its sandbox, with the paths this deployment serves routed in.
    files: BackendProtocol | None = None
    # Paths under ``files`` this deployment serves rather than the sandbox.
    backend_paths: tuple[str, ...] = ()
    # Where the run's skills are under ``files``, a later source's skill taking the
    # place of an earlier one's of the same name.
    skill_sources: tuple[str, ...] = ()
    # Where large tool results and binary reads are offloaded.
    artifacts_root: str = "/"
    # The guards on the shell commands the run's tools run, as tool-call middleware.
    shell_guards: Sequence[AgentMiddleware] = ()
    # The checks on what the run's ``read_file`` calls return.
    file_checks: tuple[FileCheck, ...] = ()
    # Whether the graph's core tools it seldom needs belong in a runtime's code mode.
    code_mode: bool = False
    # How the run reads what is sent to its thread while it runs, before each model call.
    inbox: InboxSpec | None = None
    # What the hooks around each model call are built from, without the rest of the plan.
    loop: LoopRules | None = None

    async def prepare(self) -> None:
        planned = [
            *self.base_tools,
            *(tool for middleware in self.middleware for tool in getattr(middleware, "tools", ())),
        ]
        tools = {
            tool.name: tool
            for tool in (
                item if isinstance(item, BaseTool) else as_tool(item)
                for item in planned
                if not isinstance(item, dict)
            )
        }
        if self.dynamic:
            integrations = await self.dynamic.catalog_tools()
            self.integration_names = [tool.name for tool in integrations]
            tools.update({tool.name: tool for tool in integrations})
        excluded = self.excluded | EXCLUDED_TOOLS
        self.tools = {name: tool for name, tool in tools.items() if name not in excluded}

    def catalog(self) -> list[ToolDescription]:
        return [describe_tool(name, tool) for name, tool in sorted(self.tools.items())]


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
