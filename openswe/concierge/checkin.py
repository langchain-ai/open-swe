"""Wake each concierge's background thread on a timer, to look for ways to help its person."""

import asyncio
import logging
from typing import Final

from openswe.concierge.background import Concierge
from openswe.users import User

logger = logging.getLogger(__name__)

CHECKIN_JOB: Final = "concierge_checkin"
CHECKIN_SCHEDULE: Final = "*/30 * * * *"
# Keeps the sweep under Slack's per-method rate limits; a limited DM open would skip the person.
_CHECKIN_CONCURRENCY: Final = 4


async def check_in_with_concierge_users() -> int:
    """Wake every idle concierge background thread; how many were woken."""
    users = await User.in_concierge_mode()
    gate = asyncio.Semaphore(_CHECKIN_CONCURRENCY)

    async def check_in(user: User) -> bool:
        async with gate:
            return await _check_in(user)

    return sum(await asyncio.gather(*(check_in(user) for user in users)))


async def _check_in(user: User) -> bool:
    try:
        concierge = await Concierge.of(user)
        return concierge is not None and await concierge.check_in()
    except Exception:
        logger.exception("Concierge check-in failed", extra={"user_id": str(user.id)})
        return False
