"""Send an announcement as Slack DMs, one person at a time, from a scheduler run.

The run is paced for ``conversations.open``, which Slack allows about fifty
times a minute, and waits out a rate limit rather than failing the person.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID

from openswe.announcements.models import Announcement, DeliveryStatus
from openswe.jobs import queue
from openswe.slack.dm import post_dm
from openswe.slack.http import SlackRequestError
from openswe.users import User

logger = logging.getLogger(__name__)

ANNOUNCEMENT_TASK = "announcement"
PACE_SECONDS = 1.2
_RATE_LIMIT_ATTEMPTS = 5
_DEFAULT_RETRY_AFTER = 30.0


def announcement_blocks(text: str) -> list[dict[str, Any]]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "An update from the Open SWE team"}],
        },
    ]


async def send_announcement_dm(slack_user_id: str, text: str) -> None:
    """DM one person the announcement; raises ``SlackRequestError`` with Slack's reason."""
    await post_dm(slack_user_id, text, blocks=announcement_blocks(text))


async def start_announcement(text: str, author_id: UUID | None) -> int:
    """Queue ``text`` for everyone reachable and start the run that sends it; the recipient count."""
    announcement = await Announcement.create(text, author_id)
    try:
        await queue.schedule(
            ANNOUNCEMENT_TASK,
            {"announcement_id": str(announcement.id)},
            metadata={"kind": ANNOUNCEMENT_TASK, "announcement_id": str(announcement.id)},
        )
    except Exception:
        await Announcement.delete(announcement.id)
        raise
    logger.info(
        "Announcement queued",
        extra={"announcement_id": str(announcement.id), "author_id": str(author_id)},
    )
    return await announcement.recipients()


async def deliver_announcement(announcement_id: str) -> dict[str, object]:
    """Send every queued delivery of the announcement; safe to rerun."""
    announcement = await Announcement.get(UUID(announcement_id))
    if announcement is None:
        return {"status": "missing_announcement"}
    if interrupted := await announcement.abandon_interrupted():
        logger.warning(
            "Announcement deliveries were interrupted",
            extra={"announcement_id": announcement_id, "interrupted": interrupted},
        )
    sent = failed = 0
    while (user_id := await announcement.claim_next()) is not None:
        status, error = await _deliver(announcement, user_id)
        await announcement.record(user_id, status, error)
        if status == "sent":
            sent += 1
        else:
            failed += 1
        await asyncio.sleep(PACE_SECONDS)
    await announcement.finish()
    logger.info(
        "Announcement delivered",
        extra={"announcement_id": announcement_id, "sent": sent, "failed": failed},
    )
    return {"status": "finished", "sent": sent, "failed": failed}


async def _deliver(announcement: Announcement, user_id: UUID) -> tuple[DeliveryStatus, str]:
    user = await User.get(user_id)
    if user is None or not user.slack_user_id:
        return "failed", "no_slack_identity"
    for attempt in range(1, _RATE_LIMIT_ATTEMPTS + 1):
        try:
            await send_announcement_dm(user.slack_user_id, announcement.text)
        except SlackRequestError as exc:
            delay = _retry_after(exc.code)
            if delay is None or attempt == _RATE_LIMIT_ATTEMPTS:
                return "failed", exc.code
            await asyncio.sleep(delay)
        except Exception:
            logger.exception(
                "Announcement DM failed",
                extra={"announcement_id": str(announcement.id), "user_id": str(user_id)},
            )
            return "failed", "error"
        else:
            return "sent", ""
    return "failed", "rate_limited"


def _retry_after(code: str) -> float | None:
    """Seconds to wait before retrying, or ``None`` when the error is not a rate limit."""
    if not code.startswith("rate_limited"):
        return None
    _, _, seconds = code.partition(":")
    try:
        return max(float(seconds), 1.0)
    except ValueError:
        return _DEFAULT_RETRY_AFTER
