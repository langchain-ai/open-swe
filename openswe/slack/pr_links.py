"""Passive associations between observed Slack threads and GitHub pull request links."""

import logging
import re
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.slack.client import GitHubPrRef, parse_github_pr_url
from openswe.slack.payloads import SlackEventEnvelope

logger = logging.getLogger(__name__)

_PR_URL = re.compile(
    r"https?://(?:www\.)?github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+/pull/[0-9]+(?!\w)",
    re.I,
)


def linked_pull_request_urls(value: object) -> set[str]:
    if isinstance(value, str):
        return {
            ref.url.lower()
            for match in _PR_URL.finditer(value)
            if (ref := parse_github_pr_url(match.group(0))) is not None and ref.number > 0
        }
    if isinstance(value, dict):
        return set().union(*(linked_pull_request_urls(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(linked_pull_request_urls(item) for item in value))
    return set()


def event_pull_requests(envelope: SlackEventEnvelope) -> list[GitHubPrRef]:
    event = envelope.event
    if (
        envelope.type != "event_callback"
        or event is None
        or event.type not in {"message", "app_mention"}
        or event.subtype
        not in {
            "",
            "file_share",
            "thread_broadcast",
            "bot_message",
            "message_changed",
            "me_message",
        }
    ):
        return []
    message = event.message if event.subtype == "message_changed" else event
    if message is None:
        return []
    return [
        ref
        for url in sorted(
            linked_pull_request_urls(message.model_dump(exclude={"message", "previous_message"}))
        )
        if (ref := parse_github_pr_url(url)) is not None
    ]


class SlackPullRequestLink(Base):
    __tablename__ = "slack_pull_request_link"

    team_id: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(primary_key=True)
    thread_ts: Mapped[str] = mapped_column(primary_key=True)
    pr_url: Mapped[str] = mapped_column(primary_key=True)
    message_ts: Mapped[str]
    first_seen_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def record(cls, envelope: SlackEventEnvelope) -> None:
        """Record links without changing Slack routing or requiring a managed PR."""
        event = envelope.event
        if not postgres.configured() or event is None:
            return
        refs = event_pull_requests(envelope)
        if not refs:
            return
        message = event.message if event.subtype == "message_changed" else event
        if message is None:
            return
        team_id = envelope.team_id or event.team
        channel_id = event.resolve_channel_id()
        thread_ts = message.thread_ts or message.ts
        if not (team_id and channel_id and thread_ts and message.ts):
            return
        try:
            async with postgres.session() as session:
                await session.execute(
                    insert(cls)
                    .values(
                        [
                            {
                                "team_id": team_id,
                                "channel_id": channel_id,
                                "thread_ts": thread_ts,
                                "pr_url": ref.url,
                                "message_ts": message.ts,
                            }
                            for ref in refs
                        ]
                    )
                    .on_conflict_do_nothing()
                )
        except Exception:
            logger.warning(
                "Recording Slack pull request links failed",
                extra={
                    "slack_team_id": team_id,
                    "slack_channel_id": channel_id,
                    "slack_thread_ts": thread_ts,
                },
                exc_info=True,
            )
