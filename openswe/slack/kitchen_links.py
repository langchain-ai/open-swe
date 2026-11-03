"""The kitchen channel that goes with a Slack channel, matched by name and cached in PostgreSQL.

``#team-foo`` goes with the kitchen channel ``#team-foo-kitchen``; punctuation is
ignored, so ``#team-foo_kitchen`` and ``#teamfoo-kitchen`` match too. A channel with
no kitchen is cached as well, so neither answer asks Slack or scans kitchens again
until ``LINK_TTL`` passes.
"""

import re
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.channels import SlackChannel
from openswe.users.models import User
from openswe.workspaces.rows import WorkspaceSlackChannelRow

LINK_TTL: Final = timedelta(hours=1)
_NOT_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


class KitchenLink(Base):
    __tablename__ = "slack_kitchen_link"

    channel_id: Mapped[str] = mapped_column(ForeignKey(SlackChannel.id, ondelete="CASCADE"))
    kitchen_channel_id: Mapped[str | None] = mapped_column(
        ForeignKey(SlackChannel.id, ondelete="CASCADE"), default=None
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(User.id, ondelete="SET NULL"), default=None
    )

    @staticmethod
    def stem(name: str) -> str:
        return _NOT_ALPHANUMERIC.sub("", name.lower())

    @classmethod
    async def kitchen_for(cls, channel_id: str) -> str | None:
        """The kitchen channel named after ``channel_id``, if there is one."""
        if not channel_id or not postgres.configured():
            return None
        async with postgres.session() as session:
            link = await session.scalar(
                select(cls).where(
                    cls.channel_id == channel_id,
                    cls.updated_at > func.clock_timestamp() - LINK_TTL,
                )
            )
        if link is not None:
            return link.kitchen_channel_id
        kitchen = await cls._match(channel_id)
        async with postgres.session() as session:
            await SlackChannel.ensure(session, channel_id)
            upsert = insert(cls).values(
                id=uuid7(), channel_id=channel_id, kitchen_channel_id=kitchen
            )
            await session.execute(
                upsert.on_conflict_do_update(
                    index_elements=[cls.channel_id],
                    set_={
                        "kitchen_channel_id": upsert.excluded.kitchen_channel_id,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )
        return kitchen

    @classmethod
    async def _match(cls, channel_id: str) -> str | None:
        name = await SlackChannel.name_for(channel_id)
        if not name:
            return None
        wanted = f"{cls.stem(name)}kitchen"
        async with postgres.session() as session:
            kitchens = await session.execute(
                select(SlackChannel.id, SlackChannel.name)
                .join(
                    WorkspaceSlackChannelRow, WorkspaceSlackChannelRow.channel_id == SlackChannel.id
                )
                .where(WorkspaceSlackChannelRow.kitchen)
                .order_by(SlackChannel.name)
            )
        return next(
            (
                kitchen_id
                for kitchen_id, kitchen_name in kitchens
                if kitchen_id != channel_id and cls.stem(kitchen_name) == wanted
            ),
            None,
        )
