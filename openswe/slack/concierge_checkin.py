"""Wake each person's concierge DM agent on a timer, to look for ways to help them unasked."""

import asyncio
import logging
from typing import Final

from openswe.prompts import prompt
from openswe.slack.dm import CONCIERGE_TS, open_dm
from openswe.slack.records import lookup_slack_thread_id
from openswe.slack.thread_owner import wake_thread_owner
from openswe.threads import index
from openswe.users import User

logger = logging.getLogger(__name__)

CHECKIN_JOB: Final = "concierge_checkin"
CHECKIN_SCHEDULE: Final = "*/30 * * * *"
_CHECKIN_EVENT = prompt("slack/concierge-checkin")


async def check_in_with_concierge_users() -> int:
    """Wake every idle concierge conversation; how many were woken."""
    slack_user_ids = [user.slack_user_id for user in await User.in_concierge_mode()]
    woken = await asyncio.gather(*(_check_in(user_id) for user_id in slack_user_ids if user_id))
    return sum(woken)


async def _check_in(slack_user_id: str) -> bool:
    try:
        channel_id = await open_dm(slack_user_id)
        thread_id = await lookup_slack_thread_id(channel_id, CONCIERGE_TS) if channel_id else None
        # Nothing to wake before the person's first DM, and a running agent is already helping.
        if channel_id is None or thread_id is None:
            return False
        thread = await index.get(thread_id)
        if thread is not None and thread.status == "busy":
            return False
        await wake_thread_owner(channel_id, CONCIERGE_TS, slack_user_id, _CHECKIN_EVENT)
    except Exception:
        logger.exception("Concierge check-in failed", extra={"slack_user": slack_user_id})
        return False
    return True
