"""Notify people explicitly mentioned in accepted dashboard messages."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

from openswe import event_claims
from openswe.notifications.service import AppNotification
from openswe.review.session import ReviewSessionMetadata
from openswe.slack.dm import DmOrigin
from openswe.source_context import SourceContext
from openswe.threads.store import THREADS
from openswe.users import User
from openswe.users.mentions import PersonMention
from openswe.utils.dashboard_links import dashboard_review_url, dashboard_thread_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThreadMentions:
    thread_id: str
    login: str
    message_id: str
    text: str
    _pending: ClassVar[set[asyncio.Task[None]]] = set()

    def dispatch(self) -> None:
        if not PersonMention.all_in(self.text):
            return
        task = asyncio.create_task(self.notify())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def notify(self) -> None:
        recipients = [mention.user_id for mention in PersonMention.all_in(self.text)][:10]
        if not recipients:
            return
        try:
            sender = await User.for_login("github", self.login)
            thread = await THREADS.get(self.thread_id)
            if thread is None:
                return
            review = ReviewSessionMetadata.parse(thread.metadata)
            url = (
                dashboard_review_url(review.repo_owner, review.repo_name, review.pr_number)
                if review is not None
                else dashboard_thread_url(self.thread_id)
            )
            if not url:
                return
            slack = SourceContext.from_metadata(thread.metadata).slack_thread
            origin = (
                DmOrigin(channel_id=slack.channel_id, thread_ts=slack.thread_ts)
                if slack is not None and slack.location is not None
                else None
            )
            group = f"{sender.id if sender else self.login.casefold()}:"
            for recipient_id in recipients:
                if sender is not None and recipient_id == sender.id:
                    continue
                claim_key = f"{group}{self.thread_id}:{self.message_id}:{recipient_id}"
                claimed = False
                try:
                    person = await User.get(recipient_id)
                    if person is None:
                        continue
                    if (
                        await event_claims.claim_within_limit(
                            "mention-notification",
                            claim_key,
                            ttl=timedelta(days=7),
                            group=group,
                            limit=30,
                            within=timedelta(minutes=10),
                        )
                        != "claimed"
                    ):
                        continue
                    claimed = True
                    await AppNotification.send(
                        person,
                        key=f"mention:{self.thread_id}:{self.message_id}",
                        title=f"{self.login} mentioned you in Open SWE",
                        body="Open the conversation with an account that has access.",
                        url=url,
                        fallback_text=f"{self.login} mentioned you in Open SWE. <{url}|Open conversation> "
                        "(sign in with an account that has access).",
                        origin=origin,
                        by=sender.id if sender is not None else None,
                    )
                except Exception:
                    if claimed:
                        await event_claims.release("mention-notification", claim_key)
                    logger.exception(
                        "Could not notify mentioned person",
                        extra={"thread_id": self.thread_id, "recipient": str(recipient_id)},
                    )
        except Exception:
            logger.exception(
                "Could not notify mentioned users", extra={"thread_id": self.thread_id}
            )
