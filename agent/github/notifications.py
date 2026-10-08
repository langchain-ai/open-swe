"""Slack notices for GitHub events that wake linked agent conversations."""

import hashlib
import logging
from html import escape

from langgraph_sdk import get_client

from agent.config import ENV
from agent.slack.client import (
    get_active_slack_thread,
    post_slack_thread_reply_with_ts,
    slack_thread_mutation_lock,
)
from agent.slack.http import SlackRequestError
from agent.slack.thinking import sync_slack_background_status
from agent.store import get_value, put_value

logger = logging.getLogger(__name__)


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
    namespace = ("github_review_slack_notices", thread_id)
    action = "submitted" if edited_body is None else "edited"
    outcome = {
        "approved": "Approved",
        "changes_requested": "Changes requested",
        "commented": "Comments left",
    }.get(review_state.lower())
    notice = f"{action} a review on {escape(pr_label, quote=False)}"
    suffix = f": *{outcome}*." if outcome else "."
    key = hashlib.sha256(
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
                if await get_value(namespace, key) is not None:
                    return
                await put_value(namespace, key, {"review_url": review_url, "action": action})
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
