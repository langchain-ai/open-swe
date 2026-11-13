"""The browser of a review that does work for an app, and how it signs in.

It opens pages the pull request's author controls, so MDA keeps it off the review's
sandbox and installs sign-in sessions only for the instance's review browser domains. When
the instance names a session tool, as ``<connection>/<tool>``, the review gets that single
tool of that workspace MCP connection to mint those sessions; never a personal connection,
or any other tool of that server.

Both are instance settings an instance admin sets under Defaults, which no workspace
overrides: they decide where reviews sign in, and as whom.
"""

from collections.abc import Sequence

from langchain_core.tools import BaseTool
from runtime_api.catalog import BrowserSpec, McpServerSpec

from openswe.dashboard.workspace_settings_cache import cached_instance_settings
from openswe.mcp import load_mcp_tools
from openswe.mcp.workspace import workspace_mcp_source
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG


class ReviewBrowser:
    """A review's browser and its sign-in tool."""

    @staticmethod
    async def spec() -> BrowserSpec:
        """The browser, signing in only to the instance's review browser domains."""
        domains = (await cached_instance_settings()).review_browser_domains
        return BrowserSpec(session_domains=sorted(set(domains)))

    @staticmethod
    async def session_tool() -> tuple[str, str] | None:
        """The connection and tool, by its name on its server, that mint a browser session."""
        named = (await cached_instance_settings()).review_browser_session_tool or ""
        connection, _, tool = named.partition("/")
        return (connection, tool) if connection and tool else None

    @classmethod
    async def sign_in(cls, workspace: str | None) -> tuple[list[BaseTool], list[McpServerSpec]]:
        """The session tool alone, from the workspace's connection.

        A tool the backend serves comes back as a tool; one on a server the run connects to
        itself, as that server narrowed to the tool.
        """
        named = await cls.session_tool()
        if named is None:
            return [], []
        connection, tool = named
        loaded = await load_mcp_tools(
            workspace_mcp_source(workspace or DEFAULT_WORKSPACE_SLUG), connection_name=connection
        )
        tools = [found for found in loaded if (found.metadata or {}).get("mcp_tool_name") == tool]
        return tools, cls._narrowed(loaded.runtime_servers, tool)

    @staticmethod
    def _narrowed(servers: Sequence[McpServerSpec], tool: str) -> list[McpServerSpec]:
        return [
            server.model_copy(update={"tools": {tool: server.tools[tool]}})
            for server in servers
            if tool in server.tools
        ]
