"""Storage for explicit thread feedback and historical submissions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.database.store_imports import StoreImport
from openswe.store import delete_value, search_entries
from openswe.utils.json_types import JsonObject

Rating = Literal["bad", "good", "other"]
FEEDBACK_STORE_NAMESPACE = ["thread_feedback"]


class Feedback(BaseModel):
    status: Literal["pending", "ready", "completed", "dismissed"] = "pending"
    event_id: str = ""
    answer_run_id: str = ""
    activity_at_ms: int = 0
    rating: Rating | None = None
    comment: str = ""


class ThreadFeedback(Base):
    __tablename__ = "thread_feedback"

    thread_id: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[JsonObject] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


class FeedbackRecords:
    """Each thread's feedback, one ``thread_feedback`` row per thread."""

    async def get(self, thread_id: str) -> Feedback | None:
        async with postgres.session() as session:
            value = await session.scalar(
                select(ThreadFeedback.value).where(ThreadFeedback.thread_id == thread_id)
            )
        return None if value is None else Feedback.model_validate(value)

    async def put(self, thread_id: str, record: Feedback) -> None:
        value = record.model_dump()
        async with postgres.session() as session:
            await session.execute(
                insert(ThreadFeedback)
                .values(thread_id=thread_id, value=value)
                .on_conflict_do_update(
                    index_elements=[ThreadFeedback.thread_id],
                    set_={"value": value, "updated_at": func.clock_timestamp()},
                )
            )

    async def import_store(self) -> StoreImport:
        """Move feedback from the LangGraph Store.

        A record already in PostgreSQL wins unless the Store holds the person's
        answer and PostgreSQL does not, so nobody is asked twice.
        """
        moved = 0
        answered = ("completed", "dismissed")
        while page := await search_entries(FEEDBACK_STORE_NAMESPACE):
            for entry in page:
                upsert = insert(ThreadFeedback).values(thread_id=entry.key, value=entry.value)
                if entry.value.get("status") in answered:
                    upsert = upsert.on_conflict_do_update(
                        index_elements=[ThreadFeedback.thread_id],
                        set_={"value": entry.value, "updated_at": func.clock_timestamp()},
                        where=ThreadFeedback.value["status"].astext.not_in(answered),
                    )
                else:
                    upsert = upsert.on_conflict_do_nothing()
                async with postgres.session() as session:
                    await session.execute(upsert)
                await delete_value(entry.namespace or FEEDBACK_STORE_NAMESPACE, entry.key)
            moved += len(page)
        return StoreImport(moved=moved)


FEEDBACK = FeedbackRecords()
