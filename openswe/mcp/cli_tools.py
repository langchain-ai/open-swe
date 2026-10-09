"""Session-scoped catalog and invocation of opted-in agent tools for the CLI MCP server."""

from importlib import import_module
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, JsonValue, RootModel, TypeAdapter

from openswe.web.deps import SESSION_DEP, session_is_admin

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

Access = Literal["session", "admin"]

router = APIRouter(tags=["cli-mcp-tools"])
_json = TypeAdapter(JsonValue)
_TOOL_MODULES = (
    "openswe.tools.automations",
    "openswe.tools.workspaces",
    "openswe.tools.organization_skills",
    "openswe.tools.manage_feature_flags",
    "openswe.tools.read_only_sql",
    "openswe.tools.read_store_item",
)


class CLITool(BaseModel):
    name: str
    description: str
    parameters: dict[str, JsonValue]
    access: Access


class CLIArguments(RootModel[dict[str, JsonValue]]):
    pass


class CLIResult(BaseModel):
    status: Literal["success", "error"]
    content: JsonValue


def _tools() -> dict[str, tuple[BaseTool, Access]]:
    from langchain_core.tools import StructuredTool

    from openswe.tools.mcp_exposure import EXPOSED_TOOLS

    for module in _TOOL_MODULES:
        import_module(module)
    return {
        name: (
            StructuredTool.from_function(
                coroutine=func, name=name, description=func.__doc__ or name
            ),
            access,
        )
        for name, (func, access) in EXPOSED_TOOLS.items()
    }


async def _available(session: dict[str, object]) -> dict[str, tuple[BaseTool, Access]]:
    from openswe.mcp.instance import instance_mcp_source
    from openswe.mcp.runtime import load_mcp_tools
    from openswe.mcp.user import user_mcp_source
    from openswe.mcp.workspace import workspace_mcp_source
    from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG

    admin = session_is_admin(session)
    tools = {
        name: (tool, access)
        for name, (tool, access) in _tools().items()
        if access == "session" or admin
    }
    sources = [instance_mcp_source(), workspace_mcp_source(DEFAULT_WORKSPACE_SLUG)]
    sources.append(user_mcp_source(str(session["sub"])))
    for tool in await load_mcp_tools(*sources):
        if tool.name in tools:
            raise ValueError(f"MCP tool name collides with a local tool: {tool.name}")
        tools[tool.name] = (tool, "session")
    return tools


@router.get("/cli/mcp/tools", response_model=list[CLITool])
async def cli_mcp_tools(session: dict[str, object] = SESSION_DEP) -> list[CLITool]:
    from openswe.sandboxes.tool_runtime import tool_parameters

    return [
        CLITool(
            name=name, description=tool.description, parameters=tool_parameters(tool), access=access
        )
        for name, (tool, access) in sorted((await _available(session)).items())
    ]


@router.post("/cli/mcp/tools/{name}", response_model=CLIResult)
async def cli_mcp_invoke(
    name: str, arguments: CLIArguments, session: dict[str, object] = SESSION_DEP
) -> CLIResult:
    from langchain_core.runnables import RunnableConfig
    from langgraph.config import var_child_runnable_config

    entry = (await _available(session)).get(name)
    if entry is None:
        raise HTTPException(404, "Tool is unavailable")
    tool, _access = entry
    config: RunnableConfig = {
        "configurable": {
            "source": "mcp",
            "github_login": session["sub"],
            "user_email": session.get("email"),
            "admin_thread": session_is_admin(session),
        }
    }
    token = var_child_runnable_config.set(config)
    try:
        result = await tool.ainvoke(arguments.root, config=config)
    finally:
        var_child_runnable_config.reset(token)
    return CLIResult(status="success", content=_json.validate_python(result))
