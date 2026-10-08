"""The shared shell around a button click on a human review card."""

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from openswe.human_review.lifecycle import refresh_author_dm_card
from openswe.human_review.people import Outcome
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import post_slack_ephemeral_message, slack_thread_mutation_lock
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


async def answer_click(
    request_id: str,
    *,
    channel_id: str,
    thread_ts: str,
    slack_user_id: str,
    handle: Callable[[HumanReviewRequest], Awaitable[Outcome]],
    ephemeral: bool = True,
) -> Outcome:
    """Load the card's request, run ``handle`` under the thread's lock, and answer.

    The answer is ephemeral unless the caller shows it on the clicked message itself.
    """
    try:
        request = await HumanReviewRequest.get(UUID(request_id))
    except ValueError:
        request = None
    if request is None:
        logger.warning(
            "Human review click names no request",
            extra={"request_id": request_id, "slack_user": slack_user_id},
        )
        gone = Outcome("That review request no longer exists.")
        if ephemeral:
            await post_slack_ephemeral_message(channel_id, slack_user_id, gone.message, thread_ts)
        return gone
    # A card's copy in another channel shares its thread's lock.
    lock_channel, lock_ts = request.slack_location or (channel_id, thread_ts)
    try:
        async with slack_thread_mutation_lock(
            langgraph_client(), lock_channel, lock_ts, purpose=f"human-review:{request_id}"
        ):
            # Another click may have changed the request while this one waited for the lock.
            current = await HumanReviewRequest.get(request.id)
            outcome = (
                await handle(current)
                if current is not None
                else Outcome("That review request no longer exists.")
            )
    except Exception:
        logger.exception(
            "Human review click failed",
            extra={"request_id": request_id, "kind": request.kind},
        )
        outcome = Outcome("Something went wrong recording your click. Try again.")
    # The outcome is only ever shown to the clicker, so this is the one record of why.
    logger.info(
        "Human review click answered",
        extra={
            "request_id": request_id,
            "kind": request.kind,
            "slack_user": slack_user_id,
            "outcome": outcome.message,
        },
    )
    if (
        ephemeral
        and outcome.dm_card_success
        and request.kind == "expedited"
        and channel_id == request.slack_dm_channel_id
        and await refresh_author_dm_card(request, None)
    ):
        return outcome
    if (
        ephemeral
        and outcome.message
        and not await post_slack_ephemeral_message(
            channel_id, slack_user_id, outcome.message, thread_ts
        )
    ):
        logger.warning(
            "Could not answer a human review click",
            extra={"request_id": request_id, "slack_user": slack_user_id},
        )
    return outcome
