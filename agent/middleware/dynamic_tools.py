"""Load optional integration tool schemas only when requested."""

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
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


def _merge_tool_names(current: list[str], update: list[str]) -> list[str]:
    return sorted(set(current) | set(update))


class DynamicToolState(AgentState):
    loaded_integration_tools: NotRequired[Annotated[list[str], _merge_tool_names]]


class DynamicToolMiddleware(AgentMiddleware[DynamicToolState]):
    """Expose connected integration schemas only after explicit loading."""

    state_schema = DynamicToolState

    def __init__(
        self,
        groups: Mapping[str, Sequence[BaseTool]],
        reserved_names: Collection[str] = (),
    ) -> None:
        self._tools_by_name: dict[str, BaseTool] = {}
        reserved = {"load_integration_tools", *reserved_names}
        catalog: list[str] = []
        for group, tools in groups.items():
            names: list[str] = []
            for tool in tools:
                if tool.name in reserved or tool.name in self._tools_by_name:
                    raise ValueError(f"Duplicate integration tool name: {tool.name}")
                self._tools_by_name[tool.name] = tool
                names.append(tool.name)
            if names:
                catalog.append(f"- {group}: {', '.join(sorted(names))}")

        async def load_integration_tools(
            tool_names: list[str],
            state: Annotated[DynamicToolState | None, InjectedState] = None,
            tool_call_id: Annotated[str, InjectedToolCallId] = "",
        ) -> Command:
            unknown = sorted(set(tool_names) - self._tools_by_name.keys())
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
            loaded = set(state.get("loaded_integration_tools", [])) if state else set()
            loaded.update(tool_names)
            names = sorted(loaded)
            return Command(
                update={
                    "loaded_integration_tools": names,
                    "messages": [
                        ToolMessage(
                            content=(
                                f"Loaded integration tool schemas: {', '.join(sorted(tool_names))}. "
                                "Call these tools normally on your next turn."
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )

        description = (
            "Load connected integration tool schemas before using them. Pass exact tool names, "
            "then call the loaded tools normally on your next turn.\nAvailable tools:\n"
            + "\n".join(catalog)
        )
        self.tools = [
            StructuredTool.from_function(
                coroutine=load_integration_tools,
                name="load_integration_tools",
                description=description,
            )
        ]

    async def abefore_agent(self, state: DynamicToolState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        return {"loaded_integration_tools": Overwrite([])}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        loaded = self._loaded_names(request.state)
        tools = [self._tools_by_name[name] for name in loaded if name in self._tools_by_name]
        return await handler(request.override(tools=[*request.tools, *tools]))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        name = request.tool_call["name"]
        tool = self._tools_by_name.get(name)
        if tool is None:
            return await handler(request)
        if name not in self._loaded_names(request.state):
            return ToolMessage(
                content=f"Load {name} with load_integration_tools before calling it.",
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return await handler(request.override(tool=tool))

    @staticmethod
    def _loaded_names(state: Mapping[str, Any]) -> list[str]:
        loaded = state.get("loaded_integration_tools", [])
        return loaded if isinstance(loaded, list) else []
