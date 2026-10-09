"""Alerts that can wait for a person's concierge to tell them in the conversation."""

import logging
from typing import Any

from openswe.prompts import prompt
from openswe.slack.client import get_slack_permalink
from openswe.slack.dm import CONCIERGE_TS, DmOrigin, open_dm, send_dm
from openswe.slack.thread_notes import note_for_thread_owner
from openswe.slack.thread_owner import start_thread_owner_run
from openswe.users import User

logger = logging.getLogger(__name__)


async def send_alert(
    slack_user_id: str,
    text: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    origin: DmOrigin | None = None,
) -> bool:
    """Hand ``text`` to the person's concierge to pass on; DM it as-is when they have none.

    For alerts that are not time sensitive: the concierge run costs a model call
    and decides how to word it. ``blocks`` only shape the direct DM.
    """
    if await User.concierge_mode_for_slack(slack_user_id) and await _wake_concierge(
        slack_user_id, text, origin
    ):
        return True
    return await send_dm(slack_user_id, text, blocks=blocks, origin=origin)


async def _wake_concierge(slack_user_id: str, text: str, origin: DmOrigin | None) -> bool:
    channel_id = await open_dm(slack_user_id)
    if channel_id is None:
        return False
    permalink = await get_slack_permalink(*origin.location) if origin is not None else None
    try:
        await start_thread_owner_run(
            channel_id,
            CONCIERGE_TS,
            slack_user_id,
            prompt("slack/concierge-alert", text=text, origin=origin, permalink=permalink or ""),
        )
    except Exception:
        logger.warning(
            "Could not hand an alert to the concierge; sending it as a DM",
            extra={"slack_user_id": slack_user_id, "slack_channel": channel_id},
            exc_info=True,
        )
        return False
    if origin is not None:
        await note_for_thread_owner(
            *origin.location,
            prompt("slack/alert-sent-for-thread", recipient=f"<@{slack_user_id}>", text=text),
        )
    return True
