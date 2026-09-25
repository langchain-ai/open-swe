"""Load optional integration tool schemas only when requested."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, NotRequired, TypedDict

from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_anthropic import ChatAnthropic, convert_to_anthropic_tool
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import InjectedState
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import load_prompt

logger = logging.getLogger(__name__)

_LOAD_TOOL_NAME = "load_integration_tools"

# Models that accept a tool added mid-conversation, by model ID prefix. Everything
# else receives loaded tools in ``tools``, which invalidates the prompt cache.
_ANTHROPIC_TOOL_ADDITION_MODELS = (
    "claude-opus-5",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-mythos-5",
)
# Responses API only: Chat Completions rejects ``additional_tools``.
_OPENAI_TOOL_ADDITION_MODELS = ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna")

ToolAddition = dict[str, object]
"""A provider-native content block that adds one tool from its position onward."""


class LoadedToolsArtifact(TypedDict):
    """Recorded on a successful load result; ``awrap_model_call`` anchors additions to it."""

    newly_loaded: list[str]


def _merge_tool_names(current: list[str], update: list[str]) -> list[str]:
    return sorted(set(current) | set(update))


@dataclass(frozen=True)
class IntegrationGroup:
    """A connected integration, described by name and built on request.

    Only the names reach the model up front. Building the tools is what costs —
    an MCP handshake, a credential round trip — so it waits until the agent asks
    for the group rather than running before the run's first model call.
    """

    tool_names: Sequence[str]
    load: Callable[[], Awaitable[Sequence[BaseTool]]]


class DynamicToolState(AgentState):
    loaded_integration_tools: NotRequired[Annotated[list[str], _merge_tool_names]]


@dataclass
class _Resolved:
    tools: dict[str, BaseTool] = field(default_factory=dict)
    done: bool = False


class DynamicToolMiddleware(OpenSWEMiddleware[DynamicToolState]):
    """Expose connected integration schemas only after explicit loading."""

    state_schema = DynamicToolState

    def __init__(
        self,
        groups: Mapping[str, IntegrationGroup | Sequence[BaseTool]],
        reserved_names: Collection[str] = (),
    ) -> None:
        reserved = {_LOAD_TOOL_NAME, *reserved_names}
        self._groups: dict[str, IntegrationGroup] = {}
        self._group_of: dict[str, str] = {}
        self._resolved: dict[str, _Resolved] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        catalog: list[str] = []

        for group, spec in groups.items():
            entry = spec if isinstance(spec, IntegrationGroup) else _eager_group(spec)
            names: list[str] = []
            for name in entry.tool_names:
                if name in reserved or name in self._group_of:
                    raise ValueError(f"Duplicate integration tool name: {name}")
                self._group_of[name] = group
                names.append(name)
            if not names:
                continue
            self._groups[group] = entry
            catalog.extend(f"- {name} (integration: {group})" for name in sorted(names))

        aliases = {
            alias: name
            for name, group in self._group_of.items()
            for alias in (f"{group}:{name}", f"{group}: {name}")
            if alias not in self._group_of
        }

        async def load_integration_tools(
            tool_names: list[str],
            state: Annotated[DynamicToolState | None, InjectedState] = None,
            tool_call_id: Annotated[str, InjectedToolCallId] = "",
        ) -> Command:
            normalized_names = [aliases.get(name, name) for name in tool_names]
            unknown = sorted(set(normalized_names) - self._group_of.keys())
            if unknown:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=f"Unknown integration tools: {', '.join(unknown)}",
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ]
                    }
                )
            missing = await self._build(normalized_names)
            if missing:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=(
                                    "These integration tools are unavailable right now: "
                                    f"{', '.join(missing)}. Continue without them."
                                ),
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ]
                    }
                )
            loaded = set(state.get("loaded_integration_tools", [])) if state else set()
            newly_loaded = sorted(set(normalized_names) - loaded)
            loaded.update(normalized_names)
            return Command(
                update={
                    "loaded_integration_tools": sorted(loaded),
                    "messages": [
                        ToolMessage(
                            content=(
                                "Loaded integration tool schemas: "
                                f"{', '.join(sorted(normalized_names))}. "
                                "Call these tools normally on your next turn."
                            ),
                            tool_call_id=tool_call_id,
                            artifact=LoadedToolsArtifact(newly_loaded=newly_loaded),
                        )
                    ],
                }
            )

        description = load_prompt("tools/load_integration_tools.md")
        if self._group_of:
            example_name = (
                "analyzePlan" if "analyzePlan" in self._group_of else next(iter(self._group_of))
            )
            example = json.dumps({"tool_names": [example_name]}, separators=(",", ":"))
            description += f"\nExample: {example}\nAvailable tools:\n" + "\n".join(catalog)
        self.tools = [
            StructuredTool.from_function(
                coroutine=load_integration_tools,
                name=_LOAD_TOOL_NAME,
                description=description,
            )
        ]

    @property
    def has_groups(self) -> bool:
        return bool(self._groups)

    async def catalog_tools(self) -> list[BaseTool]:
        """Resolve the connected tools for authenticated sandbox discovery."""
        await self._build(list(self._group_of))
        return [tool for name in self._group_of if (tool := self._tool(name)) is not None]

    async def _resolve(self, group: str) -> dict[str, BaseTool]:
        resolved = self._resolved.setdefault(group, _Resolved())
        if resolved.done:
            return resolved.tools
        lock = self._locks.setdefault(group, asyncio.Lock())
        async with lock:
            if resolved.done:
                return resolved.tools
            try:
                tools = await self._groups[group].load()
            except Exception:
                logger.warning("Failed to load %s integration tools", group, exc_info=True)
                tools = []
            resolved.tools = {tool.name: tool for tool in tools}
            resolved.done = True
        return resolved.tools

    async def _build(self, names: Sequence[str]) -> list[str]:
        """Build the groups behind ``names``; return the names that did not appear."""
        wanted = {self._group_of[name] for name in names if name in self._group_of}
        await asyncio.gather(*(self._resolve(group) for group in sorted(wanted)))
        return sorted(name for name in names if self._tool(name) is None)

    def _tool(self, name: str) -> BaseTool | None:
        group = self._group_of.get(name)
        if group is None:
            return None
        return self._resolved.get(group, _Resolved()).tools.get(name)

    async def abefore_agent(self, state: DynamicToolState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        if state.get("_deepagents_forked_context"):
            return {}
        return {"loaded_integration_tools": Overwrite([])}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        loaded = self._loaded_names(request.state)
        if loaded:
            await self._build(loaded)
        tools = {name: tool for name in loaded if (tool := self._tool(name)) is not None}
        build_addition = _tool_addition_builder(request.model)
        if build_addition is None or not tools:
            return await handler(request.override(tools=[*request.tools, *tools.values()]))
        additions = {
            name: addition
            for name, tool in tools.items()
            if (addition := build_addition(tool)) is not None
        }
        # Added where each tool was loaded, so every request extends the previous
        # one and the provider's prompt cache survives the load.
        anchors = {
            name: anchor for name, anchor in _anchors(request.messages).items() if name in additions
        }
        names_by_index: dict[int, list[str]] = {}
        for name, anchor in anchors.items():
            names_by_index.setdefault(_insertion_point(request.messages, anchor), []).append(name)
        messages = list(request.messages)
        for index in sorted(names_by_index, reverse=True):
            blocks: list[str | ToolAddition] = [
                additions[name] for name in sorted(names_by_index[index])
            ]
            messages.insert(index, SystemMessage(content=blocks))
        unanchored = [tool for name, tool in tools.items() if name not in anchors]
        return await handler(
            request.override(messages=messages, tools=[*request.tools, *unanchored])
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        name = request.tool_call["name"]
        if name not in self._group_of:
            return await handler(request)
        if name not in self._loaded_names(request.state):
            return ToolMessage(
                content=f"Load {name} with load_integration_tools before calling it.",
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        await self._build([name])
        tool = self._tool(name)
        if tool is None:
            return ToolMessage(
                content=f"{name} is unavailable right now. Continue without it.",
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return await handler(request.override(tool=tool))

    @staticmethod
    def _loaded_names(state: Mapping[str, Any]) -> list[str]:
        loaded = state.get("loaded_integration_tools", [])
        return loaded if isinstance(loaded, list) else []


def _unwrap_bound(model: object) -> object:
    current = model
    for _ in range(10):
        bound = getattr(current, "bound", None)
        if bound is None or bound is current:
            break
        current = bound
    return current


def _tool_addition_builder(
    model: object,
) -> Callable[[BaseTool], ToolAddition | None] | None:
    """Return how ``model`` is given a tool mid-conversation, or ``None`` if it can't be.

    The builder returns ``None`` for a tool it can't add, which stays in ``tools``.
    """
    chat_model = _unwrap_bound(model)
    if isinstance(chat_model, ChatAnthropic) and chat_model.model.startswith(
        _ANTHROPIC_TOOL_ADDITION_MODELS
    ):
        return _anthropic_tool_addition
    # Not subclasses: the desktop Codex model lifts every system message into
    # ``instructions`` and raises on a non-text block.
    if (
        type(chat_model) is ChatOpenAI
        and chat_model.use_responses_api is True
        and chat_model.model_name.startswith(_OPENAI_TOOL_ADDITION_MODELS)
    ):
        return _openai_tool_addition
    return None


def _anthropic_tool_addition(tool: BaseTool) -> ToolAddition | None:
    definition = convert_to_anthropic_tool(tool)
    # Anthropic rejects a root oneOf/anyOf, failing the whole request. ``bind_tools``
    # drops such a tool from ``tools`` instead, so leave it there.
    if isinstance(schema := definition.get("input_schema"), Mapping) and (
        "oneOf" in schema or "anyOf" in schema
    ):
        return None
    return {"type": "tool_addition", "tool": {"type": "tool_definition", "definition": definition}}


def _openai_tool_addition(tool: BaseTool) -> ToolAddition:
    # The Responses shape of the function tool ``bind_tools`` would send in ``tools``.
    function = {"type": "function", **convert_to_openai_tool(tool)["function"]}
    return {"type": "additional_tools", "role": "developer", "tools": [function]}


def _newly_loaded(artifact: object) -> list[str]:
    if not isinstance(artifact, dict):
        return []
    names = artifact.get("newly_loaded")
    if not isinstance(names, list):
        return []
    return [name for name in names if isinstance(name, str)]


def _anchors(messages: Sequence[AnyMessage]) -> dict[str, int]:
    """Index of the latest load result that newly loaded each tool.

    The per-run reset empties the loaded list and a repeat load lists nothing new,
    so the latest load result naming a tool is its first load in this run.
    """
    load_calls: set[str] = set()
    anchors: dict[str, int] = {}
    for index, message in enumerate(messages):
        if isinstance(message, AIMessage):
            load_calls.update(
                call["id"]
                for call in message.tool_calls
                if call["name"] == _LOAD_TOOL_NAME and call["id"]
            )
        elif isinstance(message, ToolMessage) and message.tool_call_id in load_calls:
            anchors.update(dict.fromkeys(_newly_loaded(message.artifact), index))
    return anchors


def _insertion_point(messages: Sequence[AnyMessage], anchor: int) -> int:
    """Index after the anchor's tool-result batch and any follow-ups queued behind it.

    Anthropic needs the addition after a user turn and before an assistant turn, so
    it may not split a tool-result batch; OpenAI needs it to keep its position. An
    empty reply is skipped too: Anthropic drops it, leaving no assistant turn there.
    """
    index = anchor + 1
    while index < len(messages) and (
        isinstance(message := messages[index], ToolMessage | HumanMessage)
        or (isinstance(message, AIMessage) and not message.content and not message.tool_calls)
    ):
        index += 1
    return index


def _eager_group(tools: Sequence[BaseTool]) -> IntegrationGroup:
    """Wrap tools that are already built, so both forms share one code path."""

    async def load() -> Sequence[BaseTool]:
        return tools

    return IntegrationGroup(tool_names=[tool.name for tool in tools], load=load)
