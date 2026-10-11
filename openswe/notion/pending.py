"""Who assigned a task that is waiting on design approval.

The design-approval event is authored by whoever approved the design, but the
PR should still open as the person who handed the task to the agent.
"""

import logging

from pydantic import BaseModel

from openswe.notion.settings import normalize_notion_id
from openswe.store import TypedStore, now_iso

logger = logging.getLogger(__name__)

_NAMESPACE = ("notion_pending_assigner",)


class PendingAssigner(BaseModel):
    user_id: str = ""
    recorded_at: str = ""


def _store() -> TypedStore[PendingAssigner]:
    return TypedStore(_NAMESPACE, PendingAssigner)


async def remember_assigner(page_id: str, user_id: str) -> None:
    try:
        await _store().put(
            normalize_notion_id(page_id), PendingAssigner(user_id=user_id, recorded_at=now_iso())
        )
    except Exception:
        logger.exception("Failed to record Notion task assigner", extra={"notion_page_id": page_id})


async def pending_assigner(page_id: str) -> str | None:
    try:
        record = await _store().get(normalize_notion_id(page_id))
    except Exception:
        logger.exception("Failed to read Notion task assigner", extra={"notion_page_id": page_id})
        return None
    return record.user_id if record and record.user_id else None


async def forget_assigner(page_id: str) -> None:
    try:
        await _store().delete(normalize_notion_id(page_id))
    except Exception:
        logger.exception("Failed to clear Notion task assigner", extra={"notion_page_id": page_id})
