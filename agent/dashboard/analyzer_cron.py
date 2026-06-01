"""Per-repo nightly continual-learning crons for the analyzer.

When a repo's bootstrap analysis completes we register one daily LangGraph cron
that fires a continual-learning run for that repo. Runs are threadless (a fresh
thread + sandbox each night) and authenticate via the GitHub App installation
token resolved inside ``get_analyzer`` (the cron carries no fresh user token).
"""

from __future__ import annotations

import hashlib
import logging

from .review_style_jobs import (
    _client,
    build_continual_run_configurable,
    build_continual_run_input,
)
from .review_styles import get_review_style, update_review_style

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "analyzer"


def _daily_schedule(full_name: str) -> str:
    """Daily cron expression, staggered per repo to avoid a thundering herd."""
    digest = int(hashlib.sha256(full_name.encode()).hexdigest(), 16)
    minute = digest % 60
    hour = 5 + (digest // 60) % 4  # 05:00–08:59 UTC
    return f"{minute} {hour} * * *"


async def ensure_continual_cron(full_name: str) -> str | None:
    """Idempotently register the per-repo nightly continual-learning cron."""
    record = await get_review_style(full_name)
    existing = record.get("continual_cron_id") if record else None
    if isinstance(existing, str) and existing:
        return existing

    try:
        cron = await _client().crons.create(
            _ASSISTANT_ID,
            schedule=_daily_schedule(full_name),
            input=build_continual_run_input(full_name),
            config={"configurable": build_continual_run_configurable(full_name)},
            metadata={"kind": "analyzer_continual", "repo": full_name},
        )
    except Exception:
        logger.exception("Failed to create continual cron for %s", full_name)
        return None

    cron_id = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    if isinstance(cron_id, str) and cron_id:
        await update_review_style(full_name, {"continual_cron_id": cron_id})
        return cron_id
    return None


async def remove_continual_cron(full_name: str) -> None:
    """Delete the per-repo continual-learning cron, if one is registered."""
    record = await get_review_style(full_name)
    cron_id = record.get("continual_cron_id") if record else None
    if not (isinstance(cron_id, str) and cron_id):
        return
    try:
        await _client().crons.delete(cron_id)
    except Exception:
        logger.debug("Could not delete continual cron %s for %s", cron_id, full_name, exc_info=True)
    await update_review_style(full_name, {"continual_cron_id": None})
