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
from ..utils.auth import refresh_github_token
from ..utils.github_checks import (
    CheckConclusion,
    fetch_review_check_run_status,
    incomplete_review_check_result,
)
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
    is_finding_reply = configurable.get("reviewer_event") == "finding_reply"
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
        current_check_run_id = metadata.get("review_check_run_id")
        configured_check_run_id = configurable.get("review_check_run_id")
        # A finding reply owns no check of its own, so it normally has nothing
        # to settle. The exception is a check handed to it by the review it
        # preempted: that one was deliberately left in progress for this run,
        # and if the run ends without publishing, only this hook can close it.
        if is_finding_reply:
            marker = metadata.get("review_check_superseded")
            if (
                not isinstance(marker, dict)
                or not isinstance(current_check_run_id, int)
                or marker.get("review_check_run_id") != current_check_run_id
            ):
                return None
            configured_check_run_id = current_check_run_id
        if isinstance(configured_check_run_id, int):
            check_run_id = configured_check_run_id
        elif isinstance(current_check_run_id, int):
            check_run_id = current_check_run_id
        else:
            return None
        owns_current_check = current_check_run_id == check_run_id
        deferred = metadata.get("review_check_deferred_result") if owns_current_check else None
        if isinstance(deferred, dict) and deferred.get("review_check_run_id") == check_run_id:
            return None
        token = get_github_token() or await refresh_github_token(config, thread_id)
        if not token:
            logger.warning("No GitHub token to settle stale review check on thread %s", thread_id)
            return None
        if not owns_current_check:
            check_status = await fetch_review_check_run_status(
                owner=owner,
                repo=repo,
                check_run_id=check_run_id,
                token=token,
            )
            if check_status != "in_progress":
                return None
        # A pending result carries the intended terminal check outcome from a
        # completed publish or an adversarial typed failure. Retry that outcome
        # instead of replacing it with a generic incomplete-review result.
        pending = metadata.get("review_check_pending_result") if owns_current_check else None
        if (
            isinstance(pending, dict)
            and pending.get("review_check_run_id") == check_run_id
            and pending.get("conclusion") in get_args(CheckConclusion)
        ):
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
            expected_check_run_id=check_run_id,
        )
        logger.info("Settled stale review check run for thread %s", thread_id)
    except Exception:
        logger.exception("Failed to settle stale review check run for thread %s", thread_id)
    return None
