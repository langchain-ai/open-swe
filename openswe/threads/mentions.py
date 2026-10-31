"""Notify people explicitly mentioned in accepted dashboard messages."""

import logging
from dataclasses import dataclass

from openswe.notifications.service import AppNotification
from openswe.review.session import ReviewSessionMetadata
from openswe.slack.client import slack_mentioned_user_ids
from openswe.slack.dm import DmOrigin
from openswe.source_context import SourceContext
from openswe.threads.store import THREADS
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_review_url, dashboard_thread_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThreadMentions:
    thread_id: str
    login: str
    message_id: str
    text: str

    async def notify(self) -> None:
        recipients = set(slack_mentioned_user_ids(self.text))
        if not recipients:
            return
        try:
            sender = await User.for_login("github", self.login)
            if sender is not None:
                recipients.discard(sender.slack_user_id)
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
            for recipient in recipients:
                person = await User.for_identity("slack", recipient)
                if sender is not None and person is not None and person.id == sender.id:
                    continue
                await AppNotification.send_to_slack(
                    recipient,
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
            logger.exception(
                "Could not notify mentioned users", extra={"thread_id": self.thread_id}
            )
