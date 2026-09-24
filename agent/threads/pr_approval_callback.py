"""Interrupt the active run with a PR approval decision.

The PR-approval card's buttons land here after :mod:`agent.threads.pr_approval`
records the decision. Interrupting (never queuing) matters because the
``open_pull_request`` tool call is either still waiting on its 60-second poll or
has already returned a pending payload: an interrupt reaches the live run so the
model can retry immediately, and a finished run simply starts the next one.
"""

import logging

from agent.dispatch import dispatch_agent_run
from agent.webhooks.common import AGENT_VERSION_METADATA

logger = logging.getLogger(__name__)


async def post_pr_approval_decision(thread_id: str, *, approved: bool) -> None:
    """Wake the thread's agent with the author's PR attribution decision."""
    decision = (
        "The PR attribution was approved. Retry the open_pull_request call exactly as "
        "before; the approval is recorded and the tool will proceed."
        if approved
        else "The PR attribution was denied. Do not open the PR as the named author. You "
        "may re-attribute the PR to a different participant only with their own approval "
        "through the same flow, and any commits must be rewritten to match that author."
    )
    try:
        await dispatch_agent_run(
            thread_id,
            decision,
            {},
            source="slack",
            metadata=AGENT_VERSION_METADATA,
            multitask_strategy="interrupt",
        )
    except Exception:
        logger.exception("Failed to deliver the PR approval decision to thread %s", thread_id)
        raise
