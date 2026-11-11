"""Tool: ``save_app_document``, for reviews that do work for apps.

The run's own repository and pull request bind every write; no argument can name another.
Files are read from the run's sandbox under ``/workspace/``.
"""

from langchain_core.tools import BaseTool
from pydantic import JsonValue, ValidationError

from openswe.prompts import apply_tool_descriptions
from openswe.review_apps.apps import ReviewApps, SandboxFile
from openswe.review_apps.browser import ReviewBrowser
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.utils.json_types import JsonObject


class ReviewAppTools:
    """The tools a review that works for apps gets, for its catalog to offer and its calls to
    find: ``save_app_document`` when an app writes, and when one browses, the one MCP tool
    that signs the browser in."""

    def __init__(self, apps: ReviewApps, workspace: str | None) -> None:
        self.apps = apps
        self.workspace = workspace
        self.tools: list[BaseTool] = (
            apply_tool_descriptions([save_app_document]) if apps.writes else []
        )

    @staticmethod
    async def of_run(cfg: RunConfig) -> ReviewAppTools | None:
        """The tools of a review that works for apps; None for any other run."""
        apps = await ReviewApps.of(cfg)
        return None if apps is None else ReviewAppTools(apps, cfg.workspace_slug)

    async def offer(self, surface: ToolSurface) -> None:
        """Adds these tools to ``surface``, with the browser and its sign-in when an app
        browses."""
        session_tools: list[BaseTool] = []
        if self.apps.browses:
            session_tools, surface.mcp_servers = await ReviewBrowser.sign_in(self.workspace)
            surface.browser = await ReviewBrowser.spec()
        surface.base_tools = [*surface.base_tools, *self.tools, *session_tools]

    async def tool(self, name: str) -> BaseTool | None:
        """The tool a call names; the sign-in tool loads only when no other matches."""
        found = next((tool for tool in self.tools if tool.name == name), None)
        if found is not None or not self.apps.browses:
            return found
        session_tools, _ = await ReviewBrowser.sign_in(self.workspace)
        return next((tool for tool in session_tools if tool.name == name), None)


async def save_app_document(
    app: str,
    collection: str,
    key: str,
    document: JsonObject,
    files: dict[str, str] | None = None,
) -> dict[str, JsonValue]:
    """Implement the `save_app_document` tool."""
    cfg = RunConfig.from_runtime()
    try:
        apps = await ReviewApps.of(cfg)
        if apps is None or cfg.thread_id is None:
            raise ValueError("this review does no work for apps")
        named = files or {}
        found = await SandboxFile.read(str(cfg.thread_id), list(named.values()))
        stored = await apps.save(
            app, collection, key, document, {name: found[path] for name, path in named.items()}
        )
    except (ValueError, ValidationError) as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "key": stored}
