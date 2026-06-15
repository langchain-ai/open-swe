"""After-agent middleware that closes a still-open review check run.

``publish_review`` normally completes the ``Open SWE Review`` check run and
clears ``review_check_run_id`` from reviewer thread metadata. If the run ends
without ever publishing (crash, model-call limit, sandbox failure), the check
would hang "in progress" on the PR forever. This hook closes it as neutral —
the review not completing is reviewer infrastructure failing, not the PR.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..reviewer_findings import get_thread_metadata
from ..reviewer_publish import settle_review_check_run
from ..utils.github_token import get_github_token

logger = logging.getLogger(__name__)


async def _settle_review_check(*, cancelled: bool) -> None:
    """Settle the tracked review check run on a non-publishing exit."""
    config = get_config()
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return
    thread_id = configurable.get("thread_id")
    repo_config = configurable.get("repo")
    if not isinstance(thread_id, str) or not thread_id or not isinstance(repo_config, dict):
        return
    owner = repo_config.get("owner")
    repo = repo_config.get("name")
    if not isinstance(owner, str) or not owner or not isinstance(repo, str) or not repo:
        return

    try:
        metadata = await get_thread_metadata(thread_id)
        if not isinstance(metadata.get("review_check_run_id"), int):
            return
        token = get_github_token()
        if not token:
            logger.warning("No GitHub token to settle stale review check on thread %s", thread_id)
            return
        # A pending result means publish_review DID finish but its completion
        # PATCH failed transiently — retry with the real conclusion instead of
        # misreporting a published review as failed.
        pending = metadata.get("review_check_pending_result")
        if cancelled:
            conclusion = "cancelled"
            title = "Review cancelled"
            summary = (
                "The Open SWE review was cancelled before it could publish, "
                "likely because the reviewer thread exceeded its runtime or "
                "context budget. Re-trigger the review by pushing a commit "
                "or re-requesting it."
            )
        elif isinstance(pending, dict) and pending.get("conclusion") in {
            "success",
            "neutral",
            "failure",
        }:
            conclusion = pending["conclusion"]
            title = str(pending.get("title") or "Review completed")
            summary = str(pending.get("summary") or "")
        else:
            # Neutral, not failure: an incomplete review is a reviewer-infra
            # problem, and a red X on the PR misreads as a code problem.
            conclusion = "neutral"
            title = "Review did not complete"
            summary = (
                "The Open SWE review run ended without publishing a review. "
                "Re-trigger the review by pushing a commit or re-requesting it."
            )
        await settle_review_check_run(
            thread_id=thread_id,
            owner=owner,
            repo=repo,
            token=token,
            conclusion=conclusion,
            title=title,
            summary=summary,
        )
        logger.info("Settled stale review check run for thread %s", thread_id)
    except Exception:
        logger.exception("Failed to settle stale review check run for thread %s", thread_id)


@after_agent
async def settle_review_check_on_exit(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Fail the tracked review check run if the run ended without publishing."""
    await _settle_review_check(cancelled=False)
    return None
