"""Load optional integration tool schemas only when requested."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.prebuilt import InjectedState
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite

logger = logging.getLogger(__name__)


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


class DynamicToolMiddleware(AgentMiddleware[DynamicToolState]):
    """Expose connected integration schemas only after explicit loading."""

    state_schema = DynamicToolState

    def __init__(
        self,
        groups: Mapping[str, IntegrationGroup | Sequence[BaseTool]],
        reserved_names: Collection[str] = (),
    ) -> None:
        reserved = {"load_integration_tools", *reserved_names}
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
                        )
                    ],
                }
            )

        description = (
            "Load connected integration tool schemas before using them. Pass exact tool names "
            "listed below, then call the loaded tools normally on your next turn."
        )
        if self._group_of:
            example_name = (
                "analyzePlan" if "analyzePlan" in self._group_of else next(iter(self._group_of))
            )
            example = json.dumps({"tool_names": [example_name]}, separators=(",", ":"))
            description += f"\nExample: {example}\nAvailable tools:\n" + "\n".join(catalog)
        self.tools = [
            StructuredTool.from_function(
                coroutine=load_integration_tools,
                name="load_integration_tools",
                description=description,
            )
        ]

    @property
    def has_groups(self) -> bool:
        return bool(self._groups)

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
        return {"loaded_integration_tools": Overwrite([])}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        loaded = self._loaded_names(request.state)
        if loaded:
            await self._build(loaded)
        tools = [tool for name in loaded if (tool := self._tool(name)) is not None]
        return await handler(request.override(tools=[*request.tools, *tools]))

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


def _eager_group(tools: Sequence[BaseTool]) -> IntegrationGroup:
    """Wrap tools that are already built, so both forms share one code path."""

    async def load() -> Sequence[BaseTool]:
        return tools

    return IntegrationGroup(tool_names=[tool.name for tool in tools], load=load)
