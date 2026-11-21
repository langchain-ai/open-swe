"""Tool: ``save_app_document``, for reviews that do work for apps.

The run's own repository and pull request bind every write; no argument can name another.
Files are read from the run's sandbox under ``/workspace/``.
"""

from typing import Literal

from langchain_core.tools import BaseTool
from pydantic import JsonValue, ValidationError

from openswe.prompts import apply_tool_descriptions
from openswe.review_apps.apps import ReviewApps, SandboxFile
from openswe.review_apps.browser import ReviewBrowser
from openswe.review_apps.outcomes import ReviewOutcomes
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.utils.json_types import JsonObject


class ReviewAppTools:
    """The tools a review that works for apps gets, for its catalog to offer and its calls to
    find: ``save_app_document`` when an app writes, and when one browses, the one MCP tool
    that signs the browser in."""

    def __init__(self, apps: ReviewApps) -> None:
        self.apps = apps
        self.tools: list[BaseTool] = apply_tool_descriptions(
            [record_app_review_outcome, *([save_app_document] if apps.writes else [])]
        )

    @staticmethod
    async def of_run(cfg: RunConfig) -> ReviewAppTools | None:
        """The tools of a review that works for apps; None for any other run."""
        apps = await ReviewApps.of(cfg)
        return None if apps is None else ReviewAppTools(apps)

    async def offer(self, surface: ToolSurface) -> None:
        """Adds these tools to ``surface``, with the browser and its sign-in when an app
        browses."""
        session_tools: list[BaseTool] = []
        if self.apps.browses:
            session_tools = await ReviewBrowser.sign_in()
            surface.browser = await ReviewBrowser.spec()
        surface.base_tools = [*surface.base_tools, *self.tools, *session_tools]

    async def tool(self, name: str) -> BaseTool | None:
        """The tool a call names; the sign-in tool loads only when no other matches."""
        found = next((tool for tool in self.tools if tool.name == name), None)
        if found is not None or not self.apps.browses:
            return found
        session_tools = await ReviewBrowser.sign_in()
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
        if cfg.run_id and cfg.head_sha:
            await ReviewOutcomes(apps.app(app).id, apps.repository, apps.number).saved(
                cfg.run_id, cfg.head_sha, collection, stored
            )
    except (ValueError, ValidationError) as exc:
        return {"success": False, "error": str(exc)}
    result: dict[str, JsonValue] = {"success": True, "key": stored}
    if url := apps.app(app).result_url(apps.repository, apps.number):
        result["url"] = url
    return result


async def record_app_review_outcome(
    app: str,
    status: Literal["complete", "blocked", "failed", "skipped"],
    reason: str = "",
    retryable: bool = False,
) -> dict[str, JsonValue]:
    """Record the outcome of this run's work for an app."""
    cfg = RunConfig.from_runtime()
    try:
        apps = await ReviewApps.of(cfg)
        if apps is None or not cfg.run_id or not cfg.head_sha:
            raise ValueError("This run has no app review to finish")
        approved = apps.app(app)
        await ReviewOutcomes(approved.id, apps.repository, apps.number).finish(
            cfg.run_id,
            cfg.head_sha,
            status,
            reason,
            requires_result=bool(approved.step.writes),
            retryable=retryable,
        )
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True}
