"""LangSmith feedback utilities."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _get_langsmith_api_key() -> str | None:
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


async def submit_langsmith_feedback(
    run_id: str,
    score: int,
    comment: str = "",
) -> bool:
    """Submit thumbs up/down feedback to LangSmith for a run."""
    api_key = _get_langsmith_api_key()
    if not api_key:
        logger.warning("No LangSmith API key configured, skipping feedback")
        return False

    try:
        from langsmith import Client

        client = Client(api_key=api_key)
        client.create_feedback(
            run_id=run_id,
            key="user_feedback",
            score=score,
            comment=comment,
            feedback_source_type="api",
        )
        return True
    except Exception:
        logger.exception("Failed to submit LangSmith feedback for run %s", run_id)
        return False
