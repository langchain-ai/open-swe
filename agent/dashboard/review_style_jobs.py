"""Kick off and sync per-repo review style analysis runs."""

from __future__ import annotations

import logging
import os
from typing import Any

from langgraph_sdk import get_client

from ..review_style_collector import (
    collect_review_samples,
    format_samples_for_analyzer,
    generate_review_style_thread_id,
)
from .review_styles import (
    get_review_style,
    has_saved_prompt,
    mark_analysis_failed,
    mark_analysis_running,
    reconcile_running_status,
    update_review_style,
)

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "review_style_analyzer"


def _client():
    """LangGraph SDK client for the current deployment (same resolution as webapp)."""
    url = os.environ.get("LANGGRAPH_URL") or os.environ.get("LANGGRAPH_URL_PROD")
    if url:
        return get_client(url=url)
    return get_client()


async def start_review_style_analysis(
    full_name: str,
    *,
    github_token: str,
    created_by: str,
) -> dict[str, Any]:
    """Collect samples, persist metadata, and start the analyzer graph."""
    owner, repo = full_name.split("/", 1)
    try:
        samples = await collect_review_samples(github_token, owner, repo)
    except Exception:
        logger.exception("Failed to collect review samples for %s", full_name)
        await mark_analysis_failed(full_name, "sample collection failed")
        record = await get_review_style(full_name)
        return record or {
            "full_name": full_name,
            "status": "failed",
            "error": "Sample collection failed. Please retry later.",
        }

    samples_text = format_samples_for_analyzer(samples)
    thread_id = generate_review_style_thread_id(owner, repo)

    client = _client()
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "review_style_full_name": full_name,
        "review_style_github_token": github_token,
        "review_style_samples_text": samples_text,
        "review_style_top_reviewers": samples.top_reviewers,
        "review_style_prs_sampled": samples.prs_scanned,
        "review_style_reviews_sampled": samples.reviews_scanned,
    }
    if not samples.samples:
        logger.info(
            "No pre-collected samples for %s (%s merged PRs scanned); analyzer will fetch via API",
            full_name,
            samples.prs_scanned,
        )

    await mark_analysis_running(
        full_name,
        thread_id=thread_id,
        run_id=None,
        top_reviewers=samples.top_reviewers,
        prs_sampled=samples.prs_scanned,
        reviews_sampled=samples.reviews_scanned,
    )

    try:
        run = await client.runs.create(
            thread_id,
            _ASSISTANT_ID,
            input={
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Analyze review style for `{full_name}`. Browse merged PR "
                            "review feedback with `GH_TOKEN=dummy gh` until you have enough "
                            "human examples, then save the repository-specific prompt."
                        ),
                    }
                ]
            },
            config={"configurable": configurable},
            if_not_exists="create",
        )
        run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
        record = await update_review_style(
            full_name,
            {"analysis_run_id": run_id, "created_by": created_by},
        )
        return record
    except Exception:
        logger.exception("Failed to start review style analyzer for %s", full_name)
        await mark_analysis_failed(full_name, "run start failed")
        record = await get_review_style(full_name)
        return record or {
            "full_name": full_name,
            "status": "failed",
            "error": "Failed to start analysis. Please retry later.",
        }


async def sync_review_style_run_status(full_name: str) -> dict[str, Any]:
    """Refresh store status from the latest analyzer run when still running."""
    record = await get_review_style(full_name)
    if not record or record.get("status") != "running":
        return record or {}

    thread_id = record.get("analysis_thread_id")
    run_id = record.get("analysis_run_id")
    if not isinstance(thread_id, str) or not thread_id:
        return record

    client = _client()
    run_status: str | None = None
    run_missing = False
    try:
        if isinstance(run_id, str) and run_id:
            run = await client.runs.get(thread_id, run_id)
        else:
            runs = await client.runs.list(thread_id, limit=1)
            items = runs if isinstance(runs, list) else (runs.get("runs") or [])
            run = items[0] if items else None
        if not run:
            run_missing = True
        else:
            raw = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
            run_status = raw.lower() if isinstance(raw, str) else None
    except Exception:
        logger.debug("Could not sync run status for %s", full_name, exc_info=True)
        return record

    return await reconcile_running_status(
        full_name, record, run_status=run_status, run_missing=run_missing
    )


async def cancel_review_style_analysis(full_name: str) -> dict[str, Any]:
    """Stop an in-flight analyzer run and clear stale ``running`` status."""
    record = await get_review_style(full_name)
    if not record:
        return {}

    if record.get("status") != "running":
        return record

    thread_id = record.get("analysis_thread_id")
    run_id = record.get("analysis_run_id")
    if isinstance(thread_id, str) and isinstance(run_id, str) and thread_id and run_id:
        try:
            await _client().runs.cancel(thread_id, run_id, wait=False)
        except Exception:
            logger.debug("Could not cancel review style run for %s", full_name, exc_info=True)

    if has_saved_prompt(record):
        return await update_review_style(full_name, {"status": "completed", "error": None})
    return await update_review_style(
        full_name,
        {"status": "idle", "error": None, "analysis_run_id": None},
    )
