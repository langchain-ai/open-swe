"""Who was @-mentioned in which observed Slack thread, to pair with the PR links it holds."""

import logging
import re
from datetime import datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.payloads import SlackEventEnvelope
from openswe.slack.pr_links import SlackPullRequestLink
from openswe.users import User

logger = logging.getLogger(__name__)

_MENTION = re.compile(r"<@([A-Z0-9_]+)(?:\|[^>]*)?>")


class SlackThreadMention(Base):
    __tablename__ = "slack_thread_mention"

    team_id: Mapped[str]
    channel_id: Mapped[str]
    thread_ts: Mapped[str]
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    message_ts: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW, init=False)

    @classmethod
    async def record(cls, envelope: SlackEventEnvelope) -> None:
        """Remember each person a posted message mentions, other than its poster, once per thread."""
        message = envelope.posted_message()
        event = envelope.event
        if not postgres.configured() or message is None or event is None:
            return
        poster = message.user if isinstance(message.user, str) else ""
        mentioned = set(_MENTION.findall(message.text or "")) - {poster}
        team_id = envelope.team_id or event.team
        channel_id = event.resolve_channel_id()
        thread_ts = message.thread_ts or message.ts
        if not (mentioned and team_id and channel_id and thread_ts and message.ts):
            return
        try:
            people = await User.for_identities("slack", mentioned | {poster})
            if unknown := mentioned - people.keys():
                logger.info(
                    "Skipping Slack mentions of people without an Open SWE account",
                    extra={"slack_channel_id": channel_id, "mentions_skipped": len(unknown)},
                )
            if not (mentioned := mentioned & people.keys()):
                return
            author = people.get(poster)
            async with postgres.session() as session:
                await session.execute(
                    insert(cls)
                    .values(
                        [
                            {
                                "id": uuid7(),
                                "team_id": team_id,
                                "channel_id": channel_id,
                                "thread_ts": thread_ts,
                                "user_id": people[slack_user_id].id,
                                "message_ts": message.ts,
                                "created_by": author.id if author else None,
                                "updated_by": author.id if author else None,
                            }
                            for slack_user_id in sorted(mentioned)
                        ]
                    )
                    .on_conflict_do_nothing()
                )
        except Exception:
            logger.warning(
                "Recording Slack thread mentions failed",
                extra={
                    "slack_team_id": team_id,
                    "slack_channel_id": channel_id,
                    "slack_thread_ts": thread_ts,
                },
                exc_info=True,
            )

    @classmethod
    async def linked_pull_requests(
        cls, user_id: UUID, *, within: timedelta, limit: int
    ) -> list[str]:
        """PR URLs linked in threads that mention this person, most recently mentioned first."""
        if not postgres.configured():
            return []
        link = SlackPullRequestLink
        mentioned_at = func.max(cls.created_at)
        async with postgres.session() as session:
            rows = await session.scalars(
                select(link.pr_url)
                .join(
                    cls,
                    (cls.team_id == link.team_id)
                    & (cls.channel_id == link.channel_id)
                    & (cls.thread_ts == link.thread_ts),
                )
                .where(
                    cls.user_id == user_id,
                    cls.created_at >= func.now() - within,
                )
                .group_by(link.pr_url)
                .order_by(mentioned_at.desc())
                .limit(limit)
            )
            return list(rows)
