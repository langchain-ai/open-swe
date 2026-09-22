"""Tool: ``record_guidance``. Records one author steering point the reviewer found in the diff."""

import logging
from typing import Any

from agent.github.pull_requests import PullRequest
from agent.review.author_guidance import GUIDANCE_CAP, GuidancePoint, SteeringHistory
from agent.review.findings import (
    ReviewerThreadMissingError,
    get_thread_id_from_runtime,
    resolve_review_head_sha,
    thread_missing_tool_result,
)
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 200
MAX_QUOTE_CHARS = 600


async def record_guidance(summary: str, quote: str) -> dict[str, Any]:
    """Implement the `record_guidance` tool."""
    trimmed_summary = summary.strip()
    trimmed_quote = quote.strip()
    if not trimmed_summary:
        return {"success": False, "error": "summary must describe what the author changed"}
    if not trimmed_quote:
        return {
            "success": False,
            "error": "quote must be copied verbatim from the message it came from",
        }
    cfg = RunConfig.from_runtime()
    if cfg.repo is None or cfg.pr_number is None:
        return {"success": False, "error": "This run is not reviewing a pull request"}
    owner, repo, number = cfg.repo.owner, cfg.repo.name, cfg.pr_number

    pull_request = await PullRequest.get(owner, repo, number)
    if pull_request is None:
        return {
            "success": False,
            "error": f"No stored pull request for {owner}/{repo}#{number}",
        }

    thread_id = get_thread_id_from_runtime()
    try:
        head_sha = await resolve_review_head_sha(thread_id, cfg)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)

    # Attribution comes from the stored turns, not from the model, which would
    # be free to invent a name; a quote matching nothing stays unattributed.
    history = await SteeringHistory.load(owner, repo, number)
    source = history.source_of(trimmed_quote) if history else None

    recorded = await GuidancePoint.record(
        pull_request,
        summary=trimmed_summary[:MAX_SUMMARY_CHARS],
        quote=trimmed_quote[:MAX_QUOTE_CHARS],
        author=source.author if source else "",
        turn_index=source.index if source else None,
        reviewer_thread_id=thread_id,
        head_sha=head_sha,
    )
    return {"success": True, "recorded": recorded, "cap": GUIDANCE_CAP}
