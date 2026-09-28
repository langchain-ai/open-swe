"""The shared shell around a button click on a human review card."""

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from agent.human_review.people import Outcome
from agent.human_review.requests import HumanReviewRequest
from agent.slack.client import post_slack_ephemeral_message, slack_thread_mutation_lock
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


async def answer_click(
    request_id: str,
    *,
    channel_id: str,
    thread_ts: str,
    slack_user_id: str,
    handle: Callable[[HumanReviewRequest], Awaitable[Outcome]],
) -> None:
    """Load the card's request, run ``handle`` under the thread's lock, and answer ephemerally."""
    try:
        request = await HumanReviewRequest.get(UUID(request_id))
    except ValueError:
        request = None
    if request is None:
        await post_slack_ephemeral_message(
            channel_id, slack_user_id, "That review request no longer exists.", thread_ts
        )
        return
    try:
        async with slack_thread_mutation_lock(
            langgraph_client(), channel_id, thread_ts, purpose=f"human-review:{request_id}"
        ):
            outcome = await handle(request)
    except Exception:
        logger.exception(
            "Human review click failed",
            extra={"request_id": request_id, "kind": request.kind},
        )
        outcome = Outcome("Something went wrong recording your click. Try again.")
    if outcome.message:
        await post_slack_ephemeral_message(channel_id, slack_user_id, outcome.message, thread_ts)
