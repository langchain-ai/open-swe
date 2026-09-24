"""Tool: ``record_guidance``. Records one author steering point the review scout found in the diff."""

import logging
import re
from typing import Any

from agent.github.pull_requests import PullRequest
from agent.review.author_guidance import GUIDANCE_CAP, GuidancePoint, SteeringHistory
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 100
MAX_QUOTE_CHARS = 600
_IDENTIFIER_RE = re.compile(r"https?://|\b[a-z]?\d{6,}\b|\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b", re.I)


async def record_guidance(summary: str, quote: str) -> dict[str, Any]:
    """Implement the `record_guidance` tool."""
    trimmed_summary = summary.strip()
    trimmed_quote = quote.strip()
    if not trimmed_summary:
        return {"success": False, "error": "summary must describe what the author changed"}
    if len(trimmed_summary) > MAX_SUMMARY_CHARS:
        return {
            "success": False,
            "error": f"summary is {len(trimmed_summary)} characters; shorten it to "
            f"{MAX_SUMMARY_CHARS} or fewer",
        }
    if _IDENTIFIER_RE.search(trimmed_summary):
        return {
            "success": False,
            "error": "summary must not contain URLs, IDs, or SHAs; describe the change in plain words",
        }
    if not trimmed_quote:
        return {
            "success": False,
            "error": "quote must be copied verbatim from the message it came from",
        }
    cfg = RunConfig.from_runtime()
    if cfg.repo is None or cfg.pr_number is None or not cfg.head_sha or not cfg.thread_id:
        return {"success": False, "error": "This run is not scouting a pull request"}
    owner, repo, number = cfg.repo.owner, cfg.repo.name, cfg.pr_number

    # Guidance is only ever a workspace user's own words: the quote must come
    # from a stored turn, so text in the diff cannot pose as steering.
    history = await SteeringHistory.load(owner, repo, number)
    if history is None:
        return {"success": False, "error": "No author messages steered this pull request"}
    source = history.source_of(trimmed_quote)
    if source is None:
        return {
            "success": False,
            "error": "quote matches none of the author's messages; copy it verbatim",
        }

    pull_request = await PullRequest(owner=owner, repo=repo, number=number).ensure()
    recorded = await GuidancePoint.record(
        pull_request,
        summary=trimmed_summary,
        quote=trimmed_quote[:MAX_QUOTE_CHARS],
        author=source.author,
        turn_index=source.index,
        reviewer_thread_id=cfg.thread_id,
        head_sha=cfg.head_sha,
    )
    return {"success": True, "recorded": recorded, "cap": GUIDANCE_CAP}
