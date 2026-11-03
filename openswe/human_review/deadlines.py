"""Durable deadlines shared by engineering and supplementary reviews."""

import logging
from datetime import UTC, datetime, timedelta

from openswe.human_review.requests import HumanReviewRequest
from openswe.jobs import queue

logger = logging.getLogger(__name__)
SCHEDULER_TASK = "human_review"


async def schedule(request: HumanReviewRequest, step: str, after: timedelta) -> bool:
    due_at = datetime.now(UTC) + after
    try:
        await queue.schedule(
            SCHEDULER_TASK,
            {"request_id": str(request.id), "step": step},
            due_at=due_at,
            metadata={"kind": SCHEDULER_TASK, "request_id": str(request.id), "step": step},
        )
    except Exception:
        logger.warning(
            "Could not schedule a human review deadline",
            extra={"request_id": str(request.id), "step": step},
            exc_info=True,
        )
        await request.log_event("deadline.failed", step=step, due_at=due_at.isoformat())
        return False
    await request.log_event("deadline.scheduled", step=step, due_at=due_at.isoformat())
    return True
