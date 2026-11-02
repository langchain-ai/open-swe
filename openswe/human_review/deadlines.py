"""Durable deadlines shared by engineering and supplementary reviews."""

import logging
from datetime import UTC, datetime, timedelta

from openswe.human_review.requests import HumanReviewRequest
from openswe.jobs import queue

logger = logging.getLogger(__name__)
SCHEDULER_TASK = "human_review"


async def schedule(request: HumanReviewRequest, step: str, after: timedelta) -> bool:
    try:
        await queue.schedule(
            SCHEDULER_TASK,
            {"request_id": str(request.id), "step": step},
            due_at=datetime.now(UTC) + after,
            metadata={"kind": SCHEDULER_TASK, "request_id": str(request.id), "step": step},
        )
    except Exception:
        logger.warning(
            "Could not schedule a human review deadline",
            extra={"request_id": str(request.id), "step": step},
            exc_info=True,
        )
        return False
    return True
