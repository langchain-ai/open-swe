"""Tool: ``record_guidance``. Records one author steering point on the reviewer thread."""

import logging
from typing import Any

from agent.review.author_guidance import (
    GUIDANCE_CAP,
    GuidanceKind,
    GuidancePoint,
    HumanTurn,
    append_guidance,
    load_steering_history,
)
from agent.review.findings import (
    ReviewerThreadMissingError,
    get_thread_id_from_runtime,
    thread_missing_tool_result,
)
from agent.run_config import RunConfig

logger = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 200
MAX_QUOTE_CHARS = 600
_KINDS: frozenset[str] = frozenset(("correction", "constraint", "direction", "preference"))


async def record_guidance(
    summary: str,
    quote: str,
    kind: GuidanceKind,
    file: str,
    start_line: int | None = None,
) -> dict[str, Any]:
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
    if kind not in _KINDS:
        return {"success": False, "error": f"Invalid kind: {kind}"}
    if not file.strip():
        return {
            "success": False,
            "error": (
                "file must name a path this PR changed where the steering is visible. "
                "Do not record a point you cannot locate in the final change."
            ),
        }

    point: GuidancePoint = {
        "summary": trimmed_summary[:MAX_SUMMARY_CHARS],
        "quote": trimmed_quote[:MAX_QUOTE_CHARS],
        "kind": kind,
        "file": file.strip(),
        "start_line": start_line,
    }
    source = await _source_turn(trimmed_quote)
    if source is not None:
        point["author"] = source.author
        point["occurred_at"] = source.created_at.isoformat()

    thread_id = get_thread_id_from_runtime()
    try:
        recorded = await append_guidance(thread_id, point)
    except ReviewerThreadMissingError as exc:
        return thread_missing_tool_result(exc)
    return {"success": True, "recorded": len(recorded), "cap": GUIDANCE_CAP}


async def _source_turn(quote: str) -> HumanTurn | None:
    """The message this quote came from.

    Attribution is stamped at write time so the review page needs no lookup, and
    is derived from the stored turns rather than asked of the model, which would
    be free to invent a name. A quote that matches nothing stays unattributed.
    """
    cfg = RunConfig.from_runtime()
    if cfg.repo is None or cfg.pr_number is None:
        return None
    try:
        history = await load_steering_history(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
    except Exception:
        logger.warning("Guidance attribution lookup failed", exc_info=True)
        return None
    if history is None:
        return None
    needle = quote.strip().strip('"').lower()
    return next((turn for turn in history.follow_ups if needle in turn.text.lower()), None)
