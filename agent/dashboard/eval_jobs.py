"""Track the reviewer eval for the admin dashboard.

The eval itself runs in the ``Reviewer eval`` GitHub Action (durable runner,
isolated from the serving deployment). The Action's harness reports progress
into a LangGraph store record (namespace ``["evals"]``, key ``"reviewer"``) via
``evals.reviewer.store_reporter``; this module reads that record for the
dashboard and reconciles a run whose heartbeat has gone stale (e.g. the Action
was killed) to ``failed``.
"""

import logging
from datetime import UTC, datetime
from typing import cast

from agent.config import configured_langgraph_url, eval_langsmith_project
from agent.review.eval_config import (
    DEFAULT_EVAL_PROJECT,
    ReviewerEvalConfig,
    resolve_config,
)
from agent.review.eval_store import (
    EVALS_NAMESPACE,
    HEARTBEAT_STALE_SECONDS,
    REVIEWER_EVAL_KEY,
    ReviewerEvalRecord,
)
from agent.store import get_value, now_iso, put_value

logger = logging.getLogger(__name__)


def _resolve_eval_config() -> ReviewerEvalConfig:
    return resolve_config(
        {
            "langsmith_project": eval_langsmith_project() or DEFAULT_EVAL_PROJECT,
            "langgraph_url": configured_langgraph_url() or "",
        }
    )


def _idle_record() -> ReviewerEvalRecord:
    config = _resolve_eval_config()
    return {
        "name": REVIEWER_EVAL_KEY,
        "status": "idle",
        "run_name": config["experiment_prefix"],
        "langsmith_project": config["langsmith_project"],
        "limit": None,
        "config_snapshot": config,
        "started_at": None,
        "finished_at": None,
        "created_by": None,
        "pid": None,
        "exit_code": None,
        "experiment_url": None,
        "error": None,
        "log_tail": None,
        "worker_id": None,
        "heartbeat": None,
        "progress": None,
        "github_run_url": None,
        "trigger": None,
        "updated_at": now_iso(),
    }


async def _get_record() -> ReviewerEvalRecord | None:
    value = await get_value(EVALS_NAMESPACE, REVIEWER_EVAL_KEY)
    return cast(ReviewerEvalRecord, value) if value is not None else None


async def _put_record(record: ReviewerEvalRecord) -> ReviewerEvalRecord:
    stamped = record.copy()
    stamped["updated_at"] = now_iso()
    await put_value(EVALS_NAMESPACE, REVIEWER_EVAL_KEY, dict(stamped))
    return stamped


def _heartbeat_age_seconds(record: ReviewerEvalRecord) -> float | None:
    """Seconds since the record's heartbeat, or ``None`` if absent/unparseable."""
    hb = record.get("heartbeat")
    if not isinstance(hb, str) or not hb:
        return None
    try:
        ts = datetime.fromisoformat(hb)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds()


def _is_heartbeat_fresh(record: ReviewerEvalRecord) -> bool:
    age = _heartbeat_age_seconds(record)
    return age is not None and age <= HEARTBEAT_STALE_SECONDS


async def get_reviewer_eval_status() -> ReviewerEvalRecord:
    """Return the latest reviewer-eval status, reconciling a stale ``running``.

    The GitHub Action refreshes the record's heartbeat while it runs. A poll
    only marks the run failed once the heartbeat is stale, so a healthy run is
    left untouched and a killed Action surfaces as ``failed`` within the stale
    threshold.
    """
    record = await _get_record()
    if record is None:
        return _idle_record()
    if record.get("status") != "running" or _is_heartbeat_fresh(record):
        return record
    stale = record.copy()
    stale["status"] = "failed"
    stale["finished_at"] = record.get("finished_at") or now_iso()
    stale["error"] = "Eval process is no longer tracked (GitHub Action stopped?)."
    return await _put_record(stale)
