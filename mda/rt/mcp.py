"""The MCP servers a run calls itself, with the headers the backend keeps for them.

The catalog names each server and the tools the run may call on it
(``runtime_api.mcp``); MDA loads them and code mode calls them by name, as it calls
the backend's operations. The model never sees their schemas on its own list: it
finds them with ``search_tools``.
"""

from collections.abc import Awaitable, Callable, Sequence

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.tools import BaseTool
from managed_deepagents import (
    HttpMcpServerConfig,
    McpServersDefinition,
    RemoteMcpServerConfig,
    SseMcpServerConfig,
    define_mcp,
)
from runtime_api.endpoint import call
from runtime_api.mcp import MCP_HEADERS, McpServerSpec
from runtime_api.operations import RunRequest

from rt.backend import OpenSweBackend
from rt.search_tools import SearchableTools


def _server_name(spec: McpServerSpec) -> str:
    # MDA names a server's tools `{server}_{tool}`, as the backend names its MCP operations.
    return f"mcp_{spec.name}"


def mcp_tool_names(specs: Sequence[McpServerSpec]) -> list[str]:
    """The names MDA gives the servers' tools, which code mode calls them by."""
    return [f"{_server_name(spec)}_{tool}" for spec in specs for tool in spec.tools]


def _server(spec: McpServerSpec, headers: dict[str, str]) -> RemoteMcpServerConfig:
    if spec.transport == "sse":
        return SseMcpServerConfig(
            transport="sse", url=spec.url, headers=headers, include_tools=spec.tools
        )
    return HttpMcpServerConfig(
        transport="http", url=spec.url, headers=headers, include_tools=spec.tools
    )


async def mcp_servers(
    backend: OpenSweBackend, specs: Sequence[McpServerSpec]
) -> list[McpServersDefinition]:
    """The servers MDA loads for the run, each with the headers the backend keeps for it.

    A launched worker reads its coordinator's catalog before it has a run of its own,
    whose servers it gets once it adopts one.
    """
    if not specs or backend.awaiting_adoption:
        return []
    headers = await call(backend, MCP_HEADERS, RunRequest())
    return [
        define_mcp(
            servers={
                _server_name(spec): _server(spec, headers.servers.get(spec.name, {}))
                for spec in specs
            },
            # A server that cannot be listed leaves the run without its tools, as on the backend.
            throw_on_load_error=False,
        )
    ]


class CodeOnlyToolsMiddleware(AgentMiddleware):
    """Keep the MCP tools MDA loads off the model's list: code mode calls them by name,
    and ``search_tools`` describes them.

    Place it inside code mode, which reads the request's tools before this drops them.
    """

    def __init__(self, names: Sequence[str], searchable: SearchableTools) -> None:
        self._names = frozenset(names)
        self._searchable = searchable

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        loaded = [
            tool
            for tool in request.tools
            if isinstance(tool, BaseTool) and tool.name in self._names
        ]
        self._searchable.add(loaded)
        offered = [tool for tool in request.tools if getattr(tool, "name", None) not in self._names]
        return await handler(request.override(tools=offered))
