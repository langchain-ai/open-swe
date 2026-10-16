"""Typed reads and writes of task properties, addressed by configured name."""

import logging
from urllib.parse import unquote

from pydantic import JsonValue

from openswe.notion.models import NotionDataSource, NotionPage, PropertyValue, RichText
from openswe.notion.settings import normalize_notion_id
from openswe.utils.repo import extract_repo_from_text

logger = logging.getLogger(__name__)


def plain_text(items: list[RichText] | None) -> str:
    return "".join(item.plain_text for item in items or []).strip()


def page_property(page: NotionPage, name: str) -> PropertyValue | None:
    return page.properties.get(name)


def property_id(page: NotionPage, name: str) -> str | None:
    prop = page_property(page, name)
    return prop.id if prop and prop.id else None


def property_was_updated(page: NotionPage, name: str, updated_ids: list[str]) -> bool:
    """Webhook property ids may be URL-encoded differently from the page's."""
    prop_id = property_id(page, name)
    if prop_id is None:
        return False
    wanted = unquote(prop_id)
    return any(unquote(updated) == wanted for updated in updated_ids)


def page_title(page: NotionPage) -> str:
    for prop in page.properties.values():
        if prop.type == "title":
            return plain_text(prop.title)
    return ""


def page_identifier(page: NotionPage) -> str:
    """``DIG-78`` from the page's ``unique_id`` property, or ``""``."""
    for prop in page.properties.values():
        if prop.type == "unique_id" and prop.unique_id and prop.unique_id.number is not None:
            prefix = prop.unique_id.prefix
            number = prop.unique_id.number
            return f"{prefix}-{number}" if prefix else str(number)
    return ""


def people_ids(page: NotionPage, name: str) -> set[str]:
    prop = page_property(page, name)
    if prop is None or prop.type != "people":
        return set()
    return {normalize_notion_id(person.id) for person in prop.people or []}


def relation_ids(page: NotionPage, name: str) -> list[str]:
    prop = page_property(page, name)
    if prop is None or prop.type != "relation":
        return []
    return [ref.id for ref in prop.relation or []]


def status_name(page: NotionPage, name: str) -> str | None:
    prop = page_property(page, name)
    if prop is None:
        return None
    option = (
        prop.status if prop.type == "status" else prop.select if prop.type == "select" else None
    )
    return option.name if option and option.name else None


def text_value(page: NotionPage, name: str) -> str:
    prop = page_property(page, name)
    if prop is None:
        return ""
    if prop.type == "select":
        return prop.select.name if prop.select else ""
    if prop.type == "multi_select":
        return prop.multi_select[0].name if prop.multi_select else ""
    if prop.type == "url":
        return prop.url or ""
    if prop.type == "rich_text":
        return plain_text(prop.rich_text)
    if prop.type == "title":
        return plain_text(prop.title)
    return ""


def repo_config(page: NotionPage, name: str, default_owner: str) -> dict[str, str] | None:
    """The repository named by the task, accepting ``owner/name``, ``name`` or a GitHub URL."""
    value = text_value(page, name).strip()
    if not value:
        return None
    already_marked = "github.com" in value or value.startswith(("repo:", "repo "))
    text = value if already_marked else f"repo:{value}"
    return extract_repo_from_text(text, default_owner=default_owner or None)


def status_patch(
    page: NotionPage, name: str, value: str, schema: NotionDataSource | None
) -> dict[str, JsonValue] | None:
    """The property update setting ``name`` to ``value``, or None when it cannot apply."""
    prop = page_property(page, name)
    if prop is None or prop.type not in ("status", "select"):
        logger.warning(
            "Notion status property missing or not a status/select",
            extra={"notion_page_id": page.id, "notion_property": name},
        )
        return None
    if schema is not None:
        schema_prop = schema.properties.get(name)
        if schema_prop is not None and value not in schema_prop.option_names():
            logger.warning(
                "Notion status option missing; skipping status update",
                extra={"notion_page_id": page.id, "notion_property": name, "notion_status": value},
            )
            return None
    return {name: {prop.type: {"name": value}}}


def url_patch(page: NotionPage, name: str, url: str) -> dict[str, JsonValue] | None:
    prop = page_property(page, name)
    if prop is None or prop.type != "url":
        logger.warning(
            "Notion PR property missing or not a URL",
            extra={"notion_page_id": page.id, "notion_property": name},
        )
        return None
    return {name: {"url": url}}


def date_text(page: NotionPage, name: str) -> str:
    prop = page_property(page, name)
    if prop is None or prop.type != "date" or prop.date is None or not prop.date.start:
        return ""
    return f"{prop.date.start} → {prop.date.end}" if prop.date.end else prop.date.start
