"""The browser of a review that does work for an app, and how it signs in.

It opens pages the pull request's author controls, so MDA keeps it off the review's
sandbox and installs sign-in sessions only for the instance's review browser domains. When
the instance names a session tool, by its name in Robin's LangSmith Managed Tools bot kit,
the review gets that single tool to mint those sessions; never a personal kit's, or any
other tool of the kit.

Both are instance settings an instance admin sets under Defaults, which no workspace
overrides: they decide where reviews sign in, and as whom.
"""

from langchain_core.tools import BaseTool
from runtime_api.catalog import BrowserSpec

from openswe.dashboard.workspace_settings_cache import cached_instance_settings
from openswe.mcp import load_mcp_tools
from openswe.mcp.managed import BotTools


class ReviewBrowser:
    """A review's browser and its sign-in tool."""

    @staticmethod
    async def spec() -> BrowserSpec:
        """The browser, signing in only to the instance's review browser domains."""
        domains = (await cached_instance_settings()).review_browser_domains
        return BrowserSpec(session_domains=sorted(set(domains)))

    @staticmethod
    async def sign_in() -> list[BaseTool]:
        """The session tool alone, from Robin's bot kit."""
        named = (await cached_instance_settings()).review_browser_session_tool
        if named is None:
            return []
        loaded = await load_mcp_tools(BotTools().source())
        return [found for found in loaded if (found.metadata or {}).get("mcp_tool_name") == named]
