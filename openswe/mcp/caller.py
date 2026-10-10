"""A person calling Open SWE's agent tools from outside any thread.

The remote MCP server serves this catalog: every agent tool opted in with
``@expose_mcp``, plus the integration MCPs the person may use in a private
thread. Each call runs through a tool node exactly as in a private thread the
caller owns, so injected state and access policies resolve the same way.
"""

from dataclasses import dataclass
from functools import cache
from importlib import import_module
from typing import TYPE_CHECKING, Final, Literal, Self

from pydantic import JsonValue

from openswe.dashboard.admin import is_admin
from openswe.sandboxes.tool_models import ToolResult

type Access = Literal["session", "admin"]

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import BaseTool

_TOOL_MODULES: Final = (
    "openswe.tools.automations",
    "openswe.tools.workspaces",
    "openswe.tools.organization_skills",
    "openswe.tools.manage_feature_flags",
    "openswe.tools.manage_review_approval_mode",
    "openswe.tools.manage_review_repos",
    "openswe.tools.read_only_sql",
    "openswe.tools.read_store_item",
    "openswe.tools.fetch_url",
    "openswe.tools.http_request",
    "openswe.tools.web_search",
    "openswe.tools.save_user_instructions",
    "openswe.tools.save_user_settings",
    "openswe.tools.user_skills",
    "openswe.tools.threads",
    "openswe.tools.report_platform_issue",
    "openswe.tools.upload_session",
    "openswe.tools.download_session",
    "openswe.slack.tools.request_pr_review",
)


class UnknownTool(LookupError):
    """The caller asked for a tool their catalog does not offer."""


@cache
def _exposed() -> dict[str, tuple[BaseTool, Access]]:
    from langchain_core.tools import StructuredTool

    from openswe.prompts import apply_tool_descriptions
    from openswe.tools.mcp_exposure import EXPOSED_TOOLS

    for module in _TOOL_MODULES:
        import_module(module)
    names = list(EXPOSED_TOOLS)
    functions = apply_tool_descriptions([func for func, _ in EXPOSED_TOOLS.values()])
    return {
        name: (
            StructuredTool.from_function(
                coroutine=func, name=name, description=func.__doc__ or name
            ),
            EXPOSED_TOOLS[name][1],
        )
        for name, func in zip(names, functions, strict=True)
    }


@dataclass(frozen=True)
class ToolCaller:
    login: str
    email: str | None

    @classmethod
    async def for_github_account(cls, external_id: str) -> Self:
        """The Open SWE user who owns this GitHub account, if they may still sign in."""
        from openswe.users import User
        from openswe.users.authorization import is_authorized_github_login

        user = await User.for_identity("github", external_id)
        if user is None or not user.github_login:
            raise PermissionError("Sign in to the Open SWE dashboard before connecting over MCP")
        if not await is_authorized_github_login(user.github_login):
            raise PermissionError(
                "Your GitHub account is not authorized for this Open SWE instance"
            )
        return cls(login=user.github_login, email=user.email or None)

    @property
    def admin(self) -> bool:
        return is_admin(self.email, login=self.login)

    def config(self) -> RunnableConfig:
        return {
            "configurable": {
                "source": "mcp",
                "github_login": self.login,
                "user_email": self.email,
                "admin_thread": self.admin,
            }
        }

    async def tools(self) -> dict[str, tuple[BaseTool, Access]]:
        from openswe.mcp.instance import instance_mcp_source
        from openswe.mcp.runtime import load_mcp_tools
        from openswe.mcp.user import user_mcp_source
        from openswe.mcp.workspace import workspace_mcp_source
        from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG

        admin = self.admin
        tools = {
            name: (tool, access)
            for name, (tool, access) in _exposed().items()
            if access == "session" or admin
        }
        sources = (
            instance_mcp_source(),
            workspace_mcp_source(DEFAULT_WORKSPACE_SLUG),
            user_mcp_source(self.login),
        )
        for tool in await load_mcp_tools(*sources):
            if tool.name in tools:
                raise ValueError(f"MCP tool name collides with a local tool: {tool.name}")
            tools[tool.name] = (tool, "session")
        return tools

    async def invoke(self, name: str, arguments: dict[str, JsonValue]) -> ToolResult:
        from langgraph.graph import MessagesState
        from langgraph.prebuilt import ToolNode

        from openswe.sandboxes.tool_runtime import invoke_tool_node

        entry = (await self.tools()).get(name)
        if entry is None:
            raise UnknownTool(name)
        tool, _access = entry
        result = await invoke_tool_node(
            ToolNode([tool]),
            MessagesState,  # ty: ignore[invalid-argument-type]
            {"messages": []},
            self.config(),
            name,
            arguments,
        )
        return ToolResult.model_validate(result)
