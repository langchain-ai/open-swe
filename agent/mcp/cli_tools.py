"""Session-scoped catalog and invocation of opted-in agent tools for the CLI MCP server."""

from importlib import import_module
from typing import Literal

from fastapi import APIRouter, HTTPException
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.config import var_child_runnable_config
from pydantic import BaseModel, JsonValue, RootModel, TypeAdapter

from agent.dashboard.deps import SESSION_DEP, session_is_admin
from agent.sandboxes.tool_runtime import tool_parameters
from agent.tools.mcp_exposure import EXPOSED_TOOLS, Access

router = APIRouter(tags=["cli-mcp-tools"])
_json = TypeAdapter(JsonValue)
_TOOL_MODULES = (
    "agent.tools.automations",
    "agent.tools.workspaces",
    "agent.tools.organization_skills",
    "agent.tools.manage_feature_flags",
    "agent.tools.read_only_sql",
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


def _available(session: dict[str, object]) -> dict[str, BaseTool]:
    admin = session_is_admin(session)
    return {name: tool for name, (tool, access) in _tools().items() if access == "session" or admin}


@router.get("/cli/mcp/tools", response_model=list[CLITool])
async def cli_mcp_tools(session: dict[str, object] = SESSION_DEP) -> list[CLITool]:
    admin = session_is_admin(session)
    return [
        CLITool(
            name=name, description=tool.description, parameters=tool_parameters(tool), access=access
        )
        for name, (tool, access) in sorted(_tools().items())
        if access == "session" or admin
    ]


@router.post("/cli/mcp/tools/{name}", response_model=CLIResult)
async def cli_mcp_invoke(
    name: str, arguments: CLIArguments, session: dict[str, object] = SESSION_DEP
) -> CLIResult:
    tool = _available(session).get(name)
    if tool is None:
        raise HTTPException(404, "Tool is unavailable")
    config = {
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
