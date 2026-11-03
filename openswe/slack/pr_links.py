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
from openswe.utils.dashboard_links import dashboard_review_pr_url

logger = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s|<>]+", re.I)


def linked_pull_requests(text: str) -> list[GitHubPrRef]:
    """Distinct GitHub PRs linked directly or through this deployment's review page."""
    refs: dict[str, GitHubPrRef] = {}
    for match in _URL.finditer(text):
        url = match.group().rstrip(".,;:!?)]\"'")
        try:
            ref = parse_github_pr_url(dashboard_review_pr_url(url) or url)
        except ValueError:
            logger.debug("Ignoring malformed PR link", exc_info=True)
            continue
        if ref is not None and ref.number > 0:
            refs[ref.url.lower()] = ref
    return list(refs.values())


def _linked_urls(value: object) -> set[str]:
    if isinstance(value, str):
        return {ref.url.lower() for ref in linked_pull_requests(value)}
    if isinstance(value, dict):
        return set().union(*(_linked_urls(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_linked_urls(item) for item in value))
    return set()


def event_pull_requests(envelope: SlackEventEnvelope) -> list[GitHubPrRef]:
    message = envelope.posted_message()
    if message is None:
        return []
    return [
        ref
        for url in sorted(_linked_urls(message.model_dump(exclude={"message", "previous_message"})))
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
    async def record(cls, envelope: SlackEventEnvelope) -> list[GitHubPrRef]:
        """Record links without changing Slack routing; returns those new to their Slack thread."""
        event = envelope.event
        if not postgres.configured() or event is None:
            return []
        refs = event_pull_requests(envelope)
        message = envelope.posted_message()
        if not refs or message is None:
            return []
        team_id = envelope.team_id or event.team
        channel_id = event.resolve_channel_id()
        thread_ts = message.thread_ts or message.ts
        if not (team_id and channel_id and thread_ts and message.ts):
            return []
        try:
            async with postgres.session() as session:
                inserted = await session.scalars(
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
                    .returning(cls.pr_url)
                )
                new_urls = set(inserted.all())
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
            return []
        return [ref for ref in refs if ref.url in new_urls]
