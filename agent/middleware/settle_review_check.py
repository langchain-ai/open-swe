"""After-agent middleware that closes a still-open review check run.

``publish_review`` normally completes the ``Open SWE Review`` check run and
clears ``review_check_run_id`` from reviewer thread metadata. If the run ends
without ever publishing (crash, model-call limit, sandbox failure), the check
would hang "in progress" on the PR forever. This hook closes it as neutral by
default, or as failure when blocking review checks are enabled.
"""

from __future__ import annotations

import logging
from typing import Any, cast, get_args

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..review.findings import get_thread_metadata
from ..review.publish import settle_review_check_run
from ..utils.github_checks import CheckConclusion, incomplete_review_check_result
from ..utils.github_token import get_github_token

logger = logging.getLogger(__name__)


@after_agent
async def settle_review_check_on_exit(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Fail the tracked review check run if the run ended without publishing."""
    config = get_config()
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    repo_config = configurable.get("repo")
    if not isinstance(thread_id, str) or not thread_id or not isinstance(repo_config, dict):
        return None
    owner = repo_config.get("owner")
    repo = repo_config.get("name")
    if not isinstance(owner, str) or not owner or not isinstance(repo, str) or not repo:
        return None

    try:
        metadata = await get_thread_metadata(thread_id)
        if not isinstance(metadata.get("review_check_run_id"), int):
            return None
        token = get_github_token()
        if not token:
            logger.warning("No GitHub token to settle stale review check on thread %s", thread_id)
            return None
        # A pending result means publish_review DID finish but its completion
        # PATCH failed transiently — retry with the real conclusion instead of
        # misreporting a published review as failed.
        pending = metadata.get("review_check_pending_result")
        if isinstance(pending, dict) and pending.get("conclusion") in get_args(CheckConclusion):
            conclusion = cast(CheckConclusion, pending["conclusion"])
            title = str(pending.get("title") or "Review completed")
            summary = str(pending.get("summary") or "")
        else:
            conclusion, title, summary = incomplete_review_check_result()
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
    return None
