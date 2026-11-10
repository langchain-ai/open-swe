"""Tools: ``save_app_document`` and ``compare_images``, for reviews that do work for apps.

The run's own repository and pull request bind every write; no argument can name another.
Files are read from, and written to, the run's sandbox under ``/workspace/``.
"""

import asyncio
import posixpath
from collections.abc import Callable

from langchain_core.tools import BaseTool
from pydantic import JsonValue, ValidationError

from openswe.prompts import apply_tool_descriptions
from openswe.review_apps.apps import ReviewApps, SandboxFile
from openswe.review_apps.browser import ReviewBrowser
from openswe.review_apps.images import ImageDifference
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.utils.json_types import JsonObject

MAX_REGIONS = 50


class ReviewAppTools:
    """The tools a review that works for apps gets, for its catalog to offer and its calls to
    find: ``save_app_document`` when an app writes, and when one browses, ``compare_images``
    and the one MCP tool that signs the browser in."""

    def __init__(self, apps: ReviewApps, workspace: str | None) -> None:
        self.apps = apps
        self.workspace = workspace
        functions: list[Callable[..., object]] = [save_app_document] if apps.writes else []
        if apps.browses:
            functions.append(compare_images)
        self.tools: list[BaseTool] = apply_tool_descriptions(functions)

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


async def compare_images(
    before: str,
    after: str,
    diff: str | None = None,
    mask: str | None = None,
    threshold: int = 0,
) -> dict[str, JsonValue]:
    """Implement the `compare_images` tool."""
    cfg = RunConfig.from_runtime()
    if cfg.thread_id is None:
        return {"success": False, "error": "comparing images needs a thread's sandbox"}
    thread_id = str(cfg.thread_id)
    stem = posixpath.splitext(after)[0]
    outputs = {"diff": diff or f"{stem}-diff.png", "mask": mask or f"{stem}-mask.png"}
    try:
        found = await SandboxFile.read(thread_id, [before, after])
        difference = await asyncio.to_thread(
            ImageDifference.of, found[before].content, found[after].content, threshold=threshold
        )
        await SandboxFile.write(
            thread_id,
            {outputs["diff"]: difference.difference, outputs["mask"]: difference.mask},
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {
        "success": True,
        "width": difference.width,
        "height": difference.height,
        "metrics": difference.metrics.model_dump(),
        "regions": [region.model_dump() for region in difference.regions[:MAX_REGIONS]],
        **outputs,
    }
