"""Best-effort writes back to a Notion task: comments, status and the PR link.

Each helper opens its own client, so it works from webhooks, tools and the
completion webhook alike. None of them raises: a Notion outage must not fail
the run or the tool that triggered the write.
"""

import logging

from pydantic import JsonValue

from openswe.notion.client import NOTION_ERRORS, notion_client, notion_writer
from openswe.notion.models import NotionDataSource, NotionPage
from openswe.notion.properties import people_ids, status_name, status_patch, url_patch
from openswe.notion.settings import NotionSettings, notion_settings

logger = logging.getLogger(__name__)


async def post_notion_comment(page_id: str, body: str, *, discussion_id: str = "") -> bool:
    """Comment on the page, or reply in ``discussion_id`` when the run started from one."""
    try:
        async with notion_writer() as client:
            await client.create_comment(page_id, body, discussion_id=discussion_id or None)
    except NOTION_ERRORS as exc:
        logger.warning(
            "Notion comment not delivered",
            extra={"notion_page_id": page_id, "error_type": type(exc).__name__},
        )
        return False
    return True


async def _schema(page: NotionPage) -> NotionDataSource | None:
    data_source_id = page.parent.data_source_id
    if not data_source_id:
        return None
    try:
        async with notion_client() as client:
            return await client.get_data_source(data_source_id)
    except NOTION_ERRORS as exc:
        logger.warning(
            "Could not read Notion data source schema",
            extra={"notion_data_source_id": data_source_id, "error_type": type(exc).__name__},
        )
        return None


async def update_task_properties(page: NotionPage, properties: dict[str, JsonValue]) -> bool:
    if not properties:
        return False
    try:
        async with notion_writer() as client:
            await client.update_page_properties(page.id, properties)
    except NOTION_ERRORS as exc:
        logger.warning(
            "Notion task properties not updated",
            extra={
                "notion_page_id": page.id,
                "notion_properties": sorted(properties),
                "error_type": type(exc).__name__,
            },
        )
        return False
    return True


async def set_task_status(page: NotionPage, status: str, settings: NotionSettings) -> bool:
    if status_name(page, settings.status_property) == status:
        return True
    patch = status_patch(page, settings.status_property, status, await _schema(page))
    return await update_task_properties(page, patch or {})


async def record_pull_request(
    page_id: str, pr_url: str, *, opened: bool, agent_task_only: bool = False
) -> None:
    """Link the PR on the task, move it to review, and say so when the task's PR changes.

    ``opened`` says whether this run created the PR or linked an existing one.
    ``agent_task_only`` records it only when the page is a task assigned to the agent.
    """
    settings = notion_settings()
    try:
        async with notion_client() as client:
            page = await client.get_page(page_id)
    except NOTION_ERRORS as exc:
        logger.warning(
            "Could not read Notion task to record its PR",
            extra={"notion_page_id": page_id, "error_type": type(exc).__name__},
        )
        return

    if agent_task_only and not (
        settings.is_task_data_source(page.parent.data_source_id)
        and people_ids(page, settings.assignee_property) & settings.agent_user_ids
    ):
        return
    prop = page.properties.get(settings.pr_property)
    already_recorded = prop is not None and prop.url == pr_url
    properties: dict[str, JsonValue] = {}
    properties.update(url_patch(page, settings.pr_property, pr_url) or {})
    # A task someone already moved past review keeps its status.
    if status_name(page, settings.status_property) in settings.startable_statuses:
        properties.update(
            status_patch(
                page, settings.status_property, settings.status_in_review, await _schema(page)
            )
            or {}
        )
    await update_task_properties(page, properties)
    if not already_recorded:
        verb = "opened" if opened else "linked"
        await post_notion_comment(page_id, f"✅ Pull request {verb}: {pr_url}")
