"""The rule that a task may only start once every linked design is approved."""

import asyncio
import logging
from dataclasses import dataclass

from openswe.notion.client import NOTION_ERRORS, NotionAPIError, NotionClient
from openswe.notion.properties import page_title, status_name
from openswe.notion.settings import NotionSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DesignDoc:
    id: str
    title: str
    url: str
    status: str | None

    def markdown_link(self) -> str:
        label = self.title or "Design document"
        return f"[{label}]({self.url})" if self.url else label


async def _load_design(client: NotionClient, page_id: str, settings: NotionSettings) -> DesignDoc:
    try:
        page = await client.get_page(page_id)
    except NOTION_ERRORS as exc:
        # An unreadable design counts as unapproved: starting work on a design
        # nobody can check would defeat the gate.
        logger.warning(
            "Could not read linked Notion design",
            extra={
                "notion_page_id": page_id,
                "error_type": type(exc).__name__,
                "notion_status_code": exc.status_code if isinstance(exc, NotionAPIError) else None,
            },
        )
        return DesignDoc(id=page_id, title="", url="", status=None)
    return DesignDoc(
        id=page.id,
        title=page_title(page),
        url=page.url,
        status=status_name(page, settings.design_status_property),
    )


async def linked_designs(
    client: NotionClient, design_ids: list[str], settings: NotionSettings
) -> list[DesignDoc]:
    unique_ids = list(dict.fromkeys(design_ids))
    return list(
        await asyncio.gather(*(_load_design(client, doc_id, settings) for doc_id in unique_ids))
    )


def unapproved(designs: list[DesignDoc], settings: NotionSettings) -> list[DesignDoc]:
    return [doc for doc in designs if doc.status != settings.design_approved_status]


def gate_comment(identifier: str, blocked_by: list[DesignDoc], approved_status: str) -> str:
    task = identifier or "this task"
    docs = "; ".join(
        f"{doc.markdown_link()} ({doc.status or 'status unavailable'})" for doc in blocked_by
    )
    return (
        f"⏸️ Can't start {task} until the Tech Design is {approved_status}: {docs}. "
        f"I'll pick it up automatically once every linked design is {approved_status}."
    )
