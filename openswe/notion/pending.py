"""Who assigned a task that is waiting on design approval.

The design-approval event is authored by whoever approved the design, but the
PR should still open as the person who handed the task to the agent.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.notion.settings import normalize_notion_id

logger = logging.getLogger(__name__)


class PendingAssigner(Base):
    __tablename__ = "notion_pending_assigner"

    notion_page_id: Mapped[str]
    notion_user_id: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    # Notion webhooks write these rows, so no person is behind them.
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    @classmethod
    async def remember(cls, page_id: str, user_id: str) -> None:
        try:
            async with postgres.session() as session:
                await session.execute(
                    insert(cls)
                    .values(
                        id=uuid7(),
                        notion_page_id=normalize_notion_id(page_id),
                        notion_user_id=user_id,
                    )
                    .on_conflict_do_update(
                        index_elements=[cls.notion_page_id],
                        set_={"notion_user_id": user_id, "updated_at": func.clock_timestamp()},
                    )
                )
        except Exception:
            logger.exception(
                "Failed to record Notion task assigner", extra={"notion_page_id": page_id}
            )

    @classmethod
    async def of(cls, page_id: str) -> str | None:
        try:
            async with postgres.session() as session:
                return await session.scalar(
                    select(cls.notion_user_id).where(
                        cls.notion_page_id == normalize_notion_id(page_id)
                    )
                )
        except Exception:
            logger.exception(
                "Failed to read Notion task assigner", extra={"notion_page_id": page_id}
            )
            return None

    @classmethod
    async def forget(cls, page_id: str) -> None:
        try:
            async with postgres.session() as session:
                await session.execute(
                    delete(cls).where(cls.notion_page_id == normalize_notion_id(page_id))
                )
        except Exception:
            logger.exception(
                "Failed to clear Notion task assigner", extra={"notion_page_id": page_id}
            )
