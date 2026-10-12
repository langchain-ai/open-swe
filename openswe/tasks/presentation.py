import logging
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from openswe.tasks import store
from openswe.utils.json_types import thread_metadata
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

type TaskEventStatus = Literal["success", "error", "timeout", "interrupted"]


class TaskEventMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    task_id: UUID
    sender_thread_id: UUID
    sender_role: store.TaskRole
    sender_label: str | None = None
    kind: Literal["message", "completion"]
    status: TaskEventStatus | None = None
    content: str

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if (self.kind == "completion") != (self.status is not None):
            raise ValueError("Only completion events carry a terminal status")
        return self


async def sender_label(thread_id: str) -> str | None:
    try:
        metadata = thread_metadata(await langgraph_client().threads.get(thread_id))
        title = metadata.get("title")
        if not isinstance(title, str) or not title.strip():
            return None
        if title.strip().lower() in {
            "untitled",
            "untitled thread",
            "new thread",
            "new chat",
            "new conversation",
            "untitled conversation",
            "worker",
            "coordinator",
        }:
            return None
        delegation = await store.TaskDelegation.get(thread_id)
        if delegation is not None and title == delegation.instructions[:80]:
            return None
        return title.strip()[:160]
    except Exception:
        logger.warning(
            "Could not load task event sender label",
            extra={"sender_thread_id": thread_id},
            exc_info=True,
        )
        return None
