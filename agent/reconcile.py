"""Reconciliation sweep: cancel runs stuck in ``pending`` past their deadline.

The durable-dispatch contract relies on the platform's completion webhook to
end every run. When that webhook never fires (crash, lost delivery), a run can
sit in ``pending`` forever and hold its thread ``busy``. This sweep is the
safety net: find busy threads, look for stale ``pending`` runs on them, and
cancel the ones older than ``max_age_seconds`` so the thread frees up.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_SEARCH_PAGE_SIZE = 100


def _parse_created_at(value: Any) -> datetime | None:
    """Parse a run's ``created_at`` into an aware UTC datetime, or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def reconcile_stale_runs(*, max_age_seconds: int = 1800) -> dict[str, int]:
    """Cancel ``pending`` runs older than ``max_age_seconds`` on busy threads.

    Walks every ``busy`` thread (paginated), lists its ``pending`` runs, and
    cancels those whose ``created_at`` is older than the cutoff. Per-thread work
    is wrapped in try/except so one bad thread never aborts the sweep.

    Returns counts: ``{"threads_checked", "stale_runs", "cancelled"}``.
    """
    client = langgraph_client()
    now = datetime.now(UTC)

    threads_checked = 0
    stale_runs = 0
    cancelled = 0

    offset = 0
    while True:
        try:
            threads = await client.threads.search(
                metadata=None,
                status="busy",
                limit=_SEARCH_PAGE_SIZE,
                offset=offset,
            )
        except Exception:
            logger.exception("Reconcile sweep: thread search failed at offset %d", offset)
            break
        if not threads:
            break

        for thread in threads:
            thread_id = thread.get("thread_id") if isinstance(thread, dict) else None
            if not thread_id:
                continue
            threads_checked += 1
            try:
                runs = await client.runs.list(thread_id, status="pending")
                stale_run_ids: list[str] = []
                for run in runs:
                    created = _parse_created_at(run.get("created_at"))
                    if created is None:
                        logger.warning(
                            "Reconcile sweep: unparseable created_at on run %s (thread %s)",
                            run.get("run_id"),
                            thread_id,
                        )
                        continue
                    if (now - created).total_seconds() <= max_age_seconds:
                        continue
                    run_id = run.get("run_id")
                    if run_id:
                        stale_run_ids.append(run_id)

                if not stale_run_ids:
                    continue
                stale_runs += len(stale_run_ids)
                await client.runs.cancel_many(
                    thread_id=thread_id,
                    run_ids=stale_run_ids,
                    action="interrupt",
                )
                cancelled += len(stale_run_ids)
                logger.info(
                    "Reconcile sweep: cancelled %d stale pending run(s) on thread %s",
                    len(stale_run_ids),
                    thread_id,
                )
            except Exception:
                logger.exception("Reconcile sweep: failed to reconcile thread %s", thread_id)
                continue

        if len(threads) < _SEARCH_PAGE_SIZE:
            break
        offset += _SEARCH_PAGE_SIZE

    counts = {
        "threads_checked": threads_checked,
        "stale_runs": stale_runs,
        "cancelled": cancelled,
    }
    logger.info("Reconcile sweep complete: %s", counts)
    return counts
