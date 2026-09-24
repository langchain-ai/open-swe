"""Watch pull requests linked in configured Slack channels and react when they close."""

import logging
import re
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.app import get_github_app_installation_id_for_repo
from agent.slack.channel_config import SlackChannelConfig
from agent.slack.client import add_slack_reaction
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

MERGED_REACTION = "merged"
CLOSED_REACTION = "x"

_PULL_REQUEST_LINK = re.compile(
    r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)/pull/([1-9][0-9]*)\b"
)


class _Owner(BaseModel):
    login: str


class _Repository(BaseModel):
    name: str
    owner: _Owner


class _PullRequest(BaseModel):
    number: int
    merged: bool = False


class PullRequestClosedEvent(BaseModel):
    repository: _Repository
    pull_request: _PullRequest


class WatchedPullRequest(Base):
    __tablename__ = "slack_watched_pull_request"

    owner: Mapped[str] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(primary_key=True)
    number: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(primary_key=True)
    message_ts: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    def linked_in(cls, text: str, *, channel_id: str, message_ts: str) -> list[Self]:
        """One watch per distinct pull request linked in a message."""
        linked = {
            (owner.lower(), repo.lower(), int(number))
            for owner, repo, number in _PULL_REQUEST_LINK.findall(text)
        }
        return [
            cls(owner=owner, repo=repo, number=number, channel_id=channel_id, message_ts=message_ts)
            for owner, repo, number in sorted(linked)
        ]

    @classmethod
    async def record(cls, watches: list[Self]) -> None:
        """Save the watches whose channel asks for them and whose repo sends close events."""
        try:
            if not watches or not postgres.configured():
                return
            if not await SlackChannelConfig.watches_pull_requests(watches[0].channel_id):
                return
            rows = [
                {
                    "owner": watch.owner,
                    "repo": watch.repo,
                    "number": watch.number,
                    "channel_id": watch.channel_id,
                    "message_ts": watch.message_ts,
                }
                for watch in watches
                if await get_github_app_installation_id_for_repo(watch.owner, watch.repo)
                is not None
            ]
            if not rows:
                return
            async with postgres.session() as session:
                await session.execute(insert(cls).values(rows).on_conflict_do_nothing())
            logger.info(
                "Watching pull requests linked in Slack",
                extra={"slack_channel": watches[0].channel_id, "pull_request_count": len(rows)},
            )
        except Exception:
            logger.exception("Failed to record pull requests linked in Slack")

    @classmethod
    async def release(cls, owner: str, repo: str, number: int) -> list[Self]:
        """Stop watching a pull request, returning the watches that were removed."""
        async with postgres.session() as session:
            removed = await session.scalars(
                delete(cls)
                .where(cls.owner == owner.lower(), cls.repo == repo.lower(), cls.number == number)
                .returning(cls)
            )
            return list(removed)

    @classmethod
    async def react_to_close(cls, payload: JsonObject) -> None:
        """React on every Slack message that linked the pull request GitHub just closed."""
        try:
            if not postgres.configured():
                return
            try:
                event = PullRequestClosedEvent.model_validate(payload)
            except ValidationError:
                logger.warning("Ignoring malformed pull_request closed payload", exc_info=True)
                return
            watches = await cls.release(
                event.repository.owner.login, event.repository.name, event.pull_request.number
            )
            reaction = MERGED_REACTION if event.pull_request.merged else CLOSED_REACTION
            enabled: dict[str, bool] = {}
            for watch in watches:
                if watch.channel_id not in enabled:
                    enabled[watch.channel_id] = await SlackChannelConfig.watches_pull_requests(
                        watch.channel_id
                    )
                if enabled[watch.channel_id]:
                    await add_slack_reaction(watch.channel_id, watch.message_ts, reaction)
        except Exception:
            logger.exception("Failed to react to a closed pull request in Slack")
