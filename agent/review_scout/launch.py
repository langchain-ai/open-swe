import logging

from agent.database import postgres
from agent.dispatch import create_durable_run
from agent.input_messages import build_run_input
from agent.invocation import new_invocation_id, with_invocation_id
from agent.prompts import render_prompt
from agent.review.walkthrough import Walkthrough
from agent.thread_ids import review_scout_thread_id

logger = logging.getLogger(__name__)

ASSISTANT_ID = "review-scout"
_SENDER_ID = "system:review-scout"


async def start_review_scout(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    base_sha: str,
    head_sha: str,
    workspace_slug: str | None,
) -> None:
    """Start a scout run for this head unless its walkthrough already exists.

    A newer head interrupts a scout still working on an older one, so only the
    latest head's walkthrough is ever written.
    """
    if not postgres.configured() or not base_sha or not head_sha:
        return
    extra = {
        "pr_repo_full_name": f"{owner}/{repo}",
        "pr_number": pr_number,
        "scout_head_sha": head_sha,
    }
    if await Walkthrough.for_head(owner, repo, pr_number, head_sha) is not None:
        logger.info("Review walkthrough already exists for head", extra=extra)
        return
    configurable = {
        "thread_id": review_scout_thread_id(owner, repo, pr_number),
        "repo": {"owner": owner, "name": repo},
        "pr_number": pr_number,
        "pr_title": pr_title,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "workspace": workspace_slug,
    }
    await create_durable_run(
        configurable["thread_id"],
        ASSISTANT_ID,
        input=build_run_input(
            render_prompt("review-scout/kickoff.md", pr_number=pr_number),
            {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
            systems=[{"id": _SENDER_ID, "display_name": "Review scout", "platform": "open-swe"}],
        ),
        source="review-scout",
        config={"configurable": with_invocation_id(configurable, new_invocation_id())},
    )
    logger.info("Started review scout", extra=extra)
