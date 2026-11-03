"""A breakout waiting for its requester to pick a destination from private buttons.

A breakout to the default destination asks first when the channel has a kitchen
named after it, or is crowded. The requester alone sees the question, with one
button per destination. A `/breakout` keeps its request here and runs again once
they choose; the agent's own breakout wakes the agent with the choice instead.
"""

import json
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.blocks import actions, block_payload, button, section
from openswe.slack.channels import SlackChannel
from openswe.slack.client import post_slack_ephemeral_message
from openswe.users.models import User
from openswe.utils.json_types import JsonObject

BUTTON_TYPE: Final = "breakout_destination"
CHOICE_LIFETIME: Final = timedelta(days=7)


class BreakoutChoice(Base):
    __tablename__ = "slack_breakout_choice"

    channel_id: Mapped[str] = mapped_column(ForeignKey(SlackChannel.id, ondelete="CASCADE"))
    thread_ts: Mapped[str]
    destination_channel_ids: Mapped[list[str]] = mapped_column(JSONB)
    # The `/breakout` to run again; NULL when the agent's breakout tool asked.
    breakout_command: Mapped[JsonObject | None] = mapped_column(JSONB, default=None)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @classmethod
    async def offer(
        cls,
        channel_id: str,
        thread_ts: str,
        slack_user_id: str,
        destinations: list[str],
        *,
        breakout_command: JsonObject | None = None,
    ) -> bool:
        """Ask the requester privately where to break out; False when they can't be asked."""
        requester = await User.for_identity("slack", slack_user_id) if slack_user_id else None
        if requester is None or not thread_ts or not postgres.configured():
            return False
        choice = cls(
            channel_id=channel_id,
            thread_ts=thread_ts,
            destination_channel_ids=destinations,
            breakout_command=breakout_command,
            created_by=requester.id,
            updated_by=requester.id,
        )
        async with postgres.session() as session:
            await session.execute(
                delete(cls).where(cls.created_at < func.clock_timestamp() - CHOICE_LIFETIME)
            )
            await SlackChannel.ensure(session, channel_id)
            session.add(choice)
        text = (
            f"Break this out in <#{destinations[0]}>, its kitchen channel, or here?"
            if destinations[0] != channel_id
            else f"<#{channel_id}> has a large audience. Break this out here anyway? "
            "To use another channel, name it in your request."
        )
        buttons = [
            button(
                "Break out here"
                if destination == channel_id
                else f"Break out in #{await SlackChannel.name_for(destination) or destination}",
                action_id=f"open_swe_option_select_breakout_{index}",
                value=json.dumps(
                    {"type": BUTTON_TYPE, "subject": str(choice.id), "action": destination}
                ),
                style="primary" if index == 0 else None,
            )
            for index, destination in enumerate(destinations)
        ]
        return await post_slack_ephemeral_message(
            channel_id,
            slack_user_id,
            text,
            thread_ts,
            blocks=block_payload([section(text), actions(*buttons)]),
        )

    @classmethod
    async def pending(cls, choice_id: str) -> BreakoutChoice | None:
        """The unexpired choice with this id, still waiting for its click."""
        try:
            key = UUID(choice_id)
        except ValueError:
            return None
        if not postgres.configured():
            return None
        async with postgres.session() as session:
            return await session.scalar(
                select(cls).where(
                    cls.id == key, cls.created_at > func.clock_timestamp() - CHOICE_LIFETIME
                )
            )

    async def claim(self) -> bool:
        """Take this choice for one click; False when another click already took it."""
        async with postgres.session() as session:
            taken = await session.scalar(
                delete(type(self)).where(type(self).id == self.id).returning(type(self).id)
            )
        return taken is not None
