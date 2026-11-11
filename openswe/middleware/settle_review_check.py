"""After-agent middleware that closes a still-open review check run.

``publish_review`` normally completes the ``Robin Review`` check run and
clears ``review_check_run_id`` from reviewer thread metadata. If the run ends
without ever publishing (crash, model-call limit, sandbox failure), the check
would hang "in progress" on the PR forever. This hook closes it as neutral —
the review not completing is reviewer infrastructure failing, not the PR.
"""

import logging

from openswe.github.thread_token import resolve_thread_github_token
from openswe.review.findings import get_thread_metadata
from openswe.review.publish import (
    ReviewCheckResult,
    TrackedReviewCheck,
    settle_review_check_run,
)
from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)


async def settle_review_check() -> None:
    """Settle the run's tracked review check run if the run ended without publishing, as the run."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id or not cfg.repo:
        return
    owner, repo = cfg.repo.owner, cfg.repo.name

    try:
        tracked = TrackedReviewCheck.model_validate(await get_thread_metadata(thread_id))
        if tracked.review_check_run_id is None:
            return
        token = await resolve_thread_github_token()
        if not token:
            logger.warning("No GitHub token to settle stale review check on thread %s", thread_id)
            return
        # A pending result means publish_review DID finish but its completion
        # PATCH failed transiently — retry with the real conclusion instead of
        # misreporting a published review as failed. Otherwise neutral, not
        # failure: an incomplete review is a reviewer-infra problem.
        result = tracked.review_check_pending_result or ReviewCheckResult(
            conclusion="neutral",
            title="Review did not complete",
            summary="The Robin review run ended without publishing a review. "
            "Re-trigger the review by pushing a commit or re-requesting it.",
        )
        await settle_review_check_run(
            thread_id=thread_id, owner=owner, repo=repo, token=token, **result.model_dump()
        )
        logger.info("Settled stale review check run for thread %s", thread_id)
    except Exception:
        logger.exception("Failed to settle stale review check run for thread %s", thread_id)
