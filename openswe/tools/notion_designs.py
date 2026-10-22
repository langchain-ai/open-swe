import logging
from typing import TypedDict

from openswe.notion.client import NOTION_ERRORS
from openswe.notion.designs import DesignPublishError, design_template, publish_design
from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)

_MAX_TITLE_CHARS = 200


class DesignTemplateResult(TypedDict, total=False):
    success: bool
    markdown: str
    truncated: bool
    error: str


class PublishDesignResult(TypedDict, total=False):
    success: bool
    url: str
    title: str
    error: str


def _task_page_id() -> str | None:
    cfg = RunConfig.from_runtime()
    page = cfg.notion_page
    if cfg.source != "notion" or page is None or not page.id or page.is_mention:
        return None
    return page.id


async def get_notion_design_template() -> DesignTemplateResult:
    """Implement the `get_notion_design_template` tool."""
    try:
        template = await design_template()
    except NOTION_ERRORS as exc:
        logger.warning(
            "Could not read Notion design template", extra={"error_type": type(exc).__name__}
        )
        return {"success": False, "error": "Could not read the design template from Notion"}
    if template is None:
        return {"success": False, "error": "No design template is configured"}
    return {"success": True, "markdown": template.markdown, "truncated": template.truncated}


async def create_notion_design(title: str, markdown: str) -> PublishDesignResult:
    """Implement the `create_notion_design` tool."""
    task_page_id = _task_page_id()
    if task_page_id is None:
        return {"success": False, "error": "This run was not started from a Notion task"}
    if not title.strip() or len(title) > _MAX_TITLE_CHARS:
        return {
            "success": False,
            "error": f"Title must be between 1 and {_MAX_TITLE_CHARS} characters",
        }
    if not markdown.strip():
        return {"success": False, "error": "Design content cannot be empty"}
    try:
        design = await publish_design(task_page_id, title, markdown)
    except DesignPublishError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "url": design.url, "title": design.title}
