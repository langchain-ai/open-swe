"""Global bot-trigger policy and persistent admission limits."""

from datetime import timedelta
from typing import Literal, cast

from openswe import event_claims
from openswe.dashboard.settings import get_instance_settings
from openswe.database.postgres import transaction
from openswe.routing.store import is_listening_channel

BotTriggerMode = Literal["mentions", "true", "false"]


class BotTriggers:
    @staticmethod
    async def mode() -> BotTriggerMode:
        value = (await get_instance_settings()).get("slack_bot_trigger_mode", "mentions")
        return cast(BotTriggerMode, value) if value in {"true", "false"} else "mentions"

    @staticmethod
    async def accepts(
        *,
        user_id: str,
        app_id: str,
        own_user_id: str,
        own_app_id: str,
        channel_id: str,
        subtype: str,
        explicit_mention: bool,
        reply_thread_ts: str,
        message_ts: str,
        dm: bool = False,
    ) -> bool:
        mode = await BotTriggers.mode()
        if (
            mode == "false"
            or not own_user_id
            or user_id == own_user_id
            or bool(app_id and app_id == own_app_id)
            or subtype not in {"", "bot_message"}
        ):
            return False
        return explicit_mention or (
            mode == "true"
            and not dm
            and (not reply_thread_ts or reply_thread_ts == message_ts)
            and await is_listening_channel(channel_id)
        )

    @staticmethod
    async def claim(
        *, team_id: str, channel_id: str, bot_id: str, message_ts: str, explicit_mention: bool
    ) -> bool:
        if explicit_mention:
            return True
        key = f"{team_id}:{channel_id}:{bot_id}"
        message = f"{key}:{message_ts}"
        async with transaction() as conn:
            if await event_claims.held("slack_bot_admitted", message, connection=conn):
                return True
            if not await event_claims.claim(
                "slack_bot_cooldown", key, ttl=timedelta(minutes=1), connection=conn
            ):
                return await event_claims.held("slack_bot_admitted", message, connection=conn)
            return await event_claims.claim(
                "slack_bot_admitted", message, ttl=timedelta(minutes=2), connection=conn
            )
