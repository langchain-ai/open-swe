"""Session-scoped catalog and invocation of opted-in agent tools for the CLI MCP server."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, JsonValue, RootModel

from openswe.dashboard.deps import SESSION_DEP
from openswe.mcp.caller import Access, ToolCaller, UnknownTool
from openswe.sandboxes.tool_models import ToolResult

router = APIRouter(tags=["cli-mcp-tools"])


class CLITool(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]
    access: Access


class CLIArguments(RootModel[dict[str, JsonValue]]):
    pass


@router.get("/cli/mcp/tools", response_model=list[CLITool])
async def cli_mcp_tools(session: dict[str, object] = SESSION_DEP) -> list[CLITool]:
    from openswe.sandboxes.tool_runtime import tool_parameters

    tools = await ToolCaller.from_session(session).tools()
    return [
        CLITool(
            name=name, description=tool.description, parameters=tool_parameters(tool), access=access
        )
        for name, (tool, access) in sorted(tools.items())
    ]


@router.post("/cli/mcp/tools/{name}", response_model=ToolResult)
async def cli_mcp_invoke(
    name: str, arguments: CLIArguments, session: dict[str, object] = SESSION_DEP
) -> ToolResult:
    try:
        return await ToolCaller.from_session(session).invoke(name, arguments.root)
    except UnknownTool:
        raise HTTPException(404, "Tool is unavailable") from None
