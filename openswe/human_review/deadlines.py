"""Durable deadlines shared by engineering and supplementary reviews."""

import logging
from datetime import UTC, datetime, timedelta

from openswe.human_review.requests import HumanReviewRequest
from openswe.jobs import queue

logger = logging.getLogger(__name__)
SCHEDULER_TASK = "human_review"
# Retries posting a ready expedited card that Slack or GitHub kept from going up.
PUBLISH_STEP = "publish"
# Copies a just-posted expedited card to its review channel and deletes the PR's older
# cards, after the request that posted it has answered.
BROADCAST_STEP = "broadcast"
_KEYED_STEPS = frozenset({PUBLISH_STEP, BROADCAST_STEP})


async def schedule(request: HumanReviewRequest, step: str, after: timedelta) -> bool:
    due_at = datetime.now(UTC) + after
    try:
        await queue.schedule(
            SCHEDULER_TASK,
            {"request_id": str(request.id), "step": step},
            due_at=due_at,
            key=f"human_review:{request.id}:{step}" if step in _KEYED_STEPS else None,
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
