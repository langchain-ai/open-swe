"""Design documents the agent publishes for review, linked from the task they gate.

A published design starts in review and is linked through the task's design
relation, so approving it resumes the task through the existing design gate.
"""

import logging
from dataclasses import dataclass

from pydantic import JsonValue

from openswe.notion.client import (
    NOTION_ERRORS,
    JsonObject,
    markdown_to_rich_text,
    notion_client,
    notion_writer,
)
from openswe.notion.models import NotionDataSource, NotionPage
from openswe.notion.properties import page_property, relation_ids
from openswe.notion.settings import NotionSettings, notion_settings

logger = logging.getLogger(__name__)


class DesignPublishError(RuntimeError):
    """A design could not be published; the message is safe to show the agent."""


@dataclass(frozen=True)
class DesignTemplate:
    markdown: str
    truncated: bool


@dataclass(frozen=True)
class PublishedDesign:
    id: str
    url: str
    title: str


async def design_template() -> DesignTemplate | None:
    """The configured template's content, or None when no template is configured."""
    template_id = notion_settings().design_template_id
    if not template_id:
        return None
    async with notion_client() as client:
        page = await client.get_page_markdown(template_id)
    return DesignTemplate(markdown=page.markdown, truncated=page.truncated)


def _design_title(title: str, prefix: str) -> str:
    title = title.strip()
    words = prefix.strip().rstrip(":").strip()
    rest = title[len(words) :]
    if words and title.lower().startswith(words.lower()) and rest[:1] in ("", ":", " "):
        title = rest.lstrip(" :")
    return f"{prefix}{title}"


def _design_properties(
    schema: NotionDataSource, title: str, settings: NotionSettings
) -> JsonObject:
    properties: JsonObject = {}
    title_name = next(
        (name for name, prop in schema.properties.items() if prop.type == "title"), ""
    )
    if not title_name:
        raise DesignPublishError("The design database has no title property")
    properties[title_name] = {"title": markdown_to_rich_text(title)}

    status = schema.properties.get(settings.design_status_property)
    review = settings.design_review_status
    if status is not None and status.type in ("status", "select") and review:
        if review in status.option_names():
            properties[settings.design_status_property] = {status.type: {"name": review}}
        else:
            logger.warning(
                "Notion design review status missing; leaving the default status",
                extra={"notion_property": status.name, "notion_status": review},
            )

    tags = schema.properties.get(settings.design_tag_property)
    if tags is not None and tags.type == "multi_select" and settings.design_tags:
        known = tags.option_names()
        missing = [tag for tag in settings.design_tags if tag not in known]
        if missing:
            logger.warning(
                "Notion design tags missing; skipping them",
                extra={"notion_property": tags.name, "notion_missing_tags": missing},
            )
        present: list[JsonValue] = [{"name": tag} for tag in settings.design_tags if tag in known]
        if present:
            properties[settings.design_tag_property] = {"multi_select": present}
    return properties


def _require_design_relation(task: NotionPage, settings: NotionSettings) -> None:
    prop = page_property(task, settings.design_property)
    if prop is None or prop.type != "relation":
        raise DesignPublishError(
            f"The task has no {settings.design_property!r} relation to link a design from"
        )


async def publish_design(task_page_id: str, title: str, markdown: str) -> PublishedDesign:
    """Create the design in review and link it from the task."""
    settings = notion_settings()
    if not settings.documents_data_source_id:
        raise DesignPublishError("No design database is configured")
    try:
        async with notion_client() as reader:
            task = await reader.get_page(task_page_id)
            schema = await reader.get_data_source(settings.documents_data_source_id)
    except NOTION_ERRORS as exc:
        logger.warning(
            "Could not read Notion task or design database",
            extra={"notion_page_id": task_page_id, "error_type": type(exc).__name__},
        )
        raise DesignPublishError("Could not read the task or the design database") from exc
    _require_design_relation(task, settings)
    design_title = _design_title(title, settings.design_title_prefix)
    properties = _design_properties(schema, design_title, settings)

    try:
        async with notion_writer() as writer:
            page = await writer.create_page(
                settings.documents_data_source_id,
                properties,
                markdown=markdown,
                icon_emoji=settings.design_icon,
            )
    except NOTION_ERRORS as exc:
        logger.warning(
            "Could not create Notion design",
            extra={"notion_page_id": task_page_id, "error_type": type(exc).__name__},
        )
        raise DesignPublishError("Notion did not accept the design document") from exc

    linked: list[JsonValue] = [
        {"id": page_id} for page_id in [*relation_ids(task, settings.design_property), page.id]
    ]
    try:
        async with notion_writer() as writer:
            await writer.update_page_properties(
                task.id, {settings.design_property: {"relation": linked}}
            )
    except NOTION_ERRORS as exc:
        logger.warning(
            "Could not link Notion design from its task",
            extra={
                "notion_page_id": task.id,
                "notion_design_id": page.id,
                "error_type": type(exc).__name__,
            },
        )
        raise DesignPublishError(
            f"Created the design at {page.url} but could not link it from the task"
        ) from exc
    return PublishedDesign(id=page.id, url=page.url, title=design_title)
