"""Slack notices for GitHub events that wake linked agent conversations."""

import hashlib
import logging
from datetime import timedelta
from html import escape

from langgraph_sdk import get_client
from pydantic import AliasPath, Field

from openswe import event_claims
from openswe.config import ENV
from openswe.github.pull_requests import PullRequest, PullRequestEvent
from openswe.slack.client import (
    add_slack_reaction,
    get_active_slack_thread,
    post_slack_thread_reply_with_ts,
    slack_thread_mutation_lock,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.thinking import sync_slack_background_status
from openswe.source_context import SourceContext
from openswe.threads.store import THREADS
from openswe.utils.preview import skip_on_preview
from openswe.workspaces.routing import is_kitchen_channel

logger = logging.getLogger(__name__)

_NOTICE_SCOPE = "github_review_slack_notice"
# Long enough to outlast GitHub's redeliveries and anyone redelivering by hand.
_NOTICE_TTL = timedelta(days=30)


class KitchenReviewEvent(PullRequestEvent):
    action: str = ""
    review_state: str = Field("", validation_alias=AliasPath("review", "state"))

    async def react(self) -> None:
        """Mark linked kitchen thread roots when GitHub receives an approval."""
        if (
            self.action != "submitted"
            or self.review_state.lower() != "approved"
            or skip_on_preview("kitchen_thread_approval_reaction")
        ):
            return
        if self.identity is None:
            return
        pull_request = await PullRequest.load(*self.identity)
        for thread_id in await pull_request.linked_threads():
            try:
                thread = await THREADS.get(thread_id)
                if thread is None:
                    continue
                location = SourceContext.from_metadata(thread.metadata).slack_location
                if location is not None and await is_kitchen_channel(location[0]):
                    await add_slack_reaction(*location, "white_check_mark")
            except Exception:
                logger.warning(
                    "Failed to react to GitHub approval in kitchen thread",
                    extra={"agent_thread_id": thread_id, "pr_url": pull_request.url},
                    exc_info=True,
                )


async def react_to_kitchen_approval(payload: dict[str, object]) -> None:
    """Handle approvals independently of whether a review wakes the agent."""
    event = KitchenReviewEvent.parse(payload)
    if event is not None:
        await event.react()


async def notify_slack_review(
    thread_id: str,
    *,
    reviewer: str,
    review_url: str,
    pr_label: str,
    review_state: str = "",
    edited_body: str | None = None,
    edited_at: str | None = None,
) -> None:
    """Attempt each review notice once, even if Slack's response is lost."""
    client = get_client(url=ENV.LANGGRAPH_URL.get())
    action = "submitted" if edited_body is None else "edited"
    outcome = {
        "approved": "Approved",
        "changes_requested": "Changes requested",
        "commented": "Comments left",
    }.get(review_state.lower())
    notice = f"{action} a review on {escape(pr_label, quote=False)}"
    suffix = f": *{outcome}*." if outcome else "."
    digest = hashlib.sha256(
        f"{review_url}:{action}:{edited_at or edited_body or ''}".encode()
    ).hexdigest()
    try:
        location = await get_active_slack_thread(client, thread_id)
        while location is not None:
            async with slack_thread_mutation_lock(
                client, location["channel_id"], location["thread_ts"], thread_id=thread_id
            ) as active:
                if active != location:
                    location = active
                    continue
                if not await event_claims.claim(
                    _NOTICE_SCOPE, f"{thread_id}:{digest}", ttl=_NOTICE_TTL
                ):
                    return
                try:
                    await post_slack_thread_reply_with_ts(
                        location["channel_id"],
                        location["thread_ts"],
                        f"@{escape(reviewer, quote=False)} <{review_url}|{notice}>{suffix}",
                        agent_thread_id=thread_id,
                        unfurl_links=False,
                        unfurl_media=False,
                    )
                except SlackRequestError as exc:
                    logger.warning(
                        "Failed to post GitHub review notice to Slack",
                        extra={"agent_thread_id": thread_id, "slack_error": exc.code},
                    )
                else:
                    await sync_slack_background_status(client, thread_id, resume=True)
                return
    except Exception:
        logger.warning(
            "Failed to notify Slack of GitHub review wake-up",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
