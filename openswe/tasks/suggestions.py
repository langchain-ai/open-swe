"""Durable future work, independent of the thread that found it."""

import json
import logging
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from pydantic import BaseModel, Field
from sqlalchemy import text

from openswe.database import transaction
from openswe.webhooks.event_log import EventLog, EventRefs

logger = logging.getLogger(__name__)


class SuggestedTaskContent(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    details: str = Field(min_length=1, max_length=20000)


class SuggestedTask(SuggestedTaskContent):
    workspace_id: UUID
    repository_id: UUID
    id: UUID = Field(default_factory=uuid7)
    thread_id: str | None = None
    task_id: UUID | None = None
    pull_request_id: UUID | None = None
    triggered_by: UUID | None = None
    context_links: list[str] = Field(default_factory=list)
    created_by: UUID | None = None
    updated_by: UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    async def create(self) -> Self:
        """Save the suggestion and publish its target-scoped workspace event."""
        async with transaction() as conn:
            result = await conn.execute(
                text("""
                    INSERT INTO suggested_task (
                        id, summary, details, workspace_id, repository_id,
                        thread_id, task_id, pull_request_id, triggered_by,
                        context_links, created_by, updated_by
                    ) VALUES (
                        :id, :summary, :details, :workspace_id, :repository_id,
                        :thread_id, :task_id, :pull_request_id, :triggered_by,
                        CAST(:context_links AS jsonb), :created_by, :updated_by
                    ) RETURNING *
                """),
                {
                    **self.model_dump(exclude={"created_at", "updated_at"}),
                    "context_links": json.dumps(self.context_links),
                },
            )
            saved = type(self).model_validate(dict(result.mappings().one()))
            repository = await conn.scalar(
                text("SELECT key FROM repository WHERE id = :id"), {"id": self.repository_id}
            )
        if not await EventLog.append(
            "workspace",
            saved.model_dump(mode="json"),
            event_type="task.suggested",
            delivery_id=str(saved.id),
            refs=EventRefs(workspace_id=self.workspace_id, github_repository=str(repository)),
        ):
            logger.error(
                "Suggested task saved without its event",
                extra={"suggested_task_id": str(saved.id)},
            )
        return saved
