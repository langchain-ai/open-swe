"""Tool: persist synthesized per-repo review style prompt."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_config

from ..dashboard.analyzer_cron import ensure_continual_cron
from ..dashboard.review_styles import mark_analysis_completed, mark_analysis_failed

logger = logging.getLogger(__name__)


async def _complete_and_register(full_name: str, **completed_kwargs: Any) -> dict[str, Any]:
    """Persist the prompt, then ensure the repo's nightly continual cron exists.

    Cron registration is idempotent, so continual runs completing later don't
    re-register it; it just guarantees a cron once a prompt first exists.
    """
    record = await mark_analysis_completed(full_name, **completed_kwargs)
    try:
        await ensure_continual_cron(full_name)
    except Exception:
        logger.exception("Failed to ensure continual cron for %s", full_name)
    return record


async def save_review_style_prompt(
    custom_prompt: str,
    analysis_summary: str = "",
    top_reviewers: str = "",
    prs_sampled: int = 0,
    reviews_sampled: int = 0,
) -> dict[str, Any]:
    """Save the synthesized repository-specific review style prompt.

    Call this once at the end of style analysis with the final prompt text
    that should be injected into the reviewer agent for this repository.
    """
    config = get_config()
    configurable = config.get("configurable") or {}
    full_name = configurable.get("review_style_full_name")
    if not isinstance(full_name, str) or "/" not in full_name:
        return {"ok": False, "error": "review_style_full_name missing from config"}

    reviewers_from_args = [r.strip() for r in top_reviewers.split(",") if r.strip()]
    reviewers_from_config = configurable.get("review_style_top_reviewers") or []
    merged_reviewers = reviewers_from_args or (
        list(reviewers_from_config) if isinstance(reviewers_from_config, list) else []
    )
    prs_count = prs_sampled or int(configurable.get("review_style_prs_sampled") or 0)
    reviews_count = reviews_sampled or int(configurable.get("review_style_reviews_sampled") or 0)

    if not custom_prompt.strip():
        await mark_analysis_failed(full_name, "custom_prompt was empty")
        return {"ok": False, "error": "custom_prompt cannot be empty"}

    record = await _complete_and_register(
        full_name,
        custom_prompt=custom_prompt.strip(),
        analysis_summary=analysis_summary.strip(),
        top_reviewers=merged_reviewers,
        prs_sampled=prs_count,
        reviews_sampled=reviews_count,
    )
    return {"ok": True, "full_name": full_name, "status": record.get("status")}
