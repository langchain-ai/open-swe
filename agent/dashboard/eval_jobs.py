"""Track the reviewer eval for the admin dashboard.

The eval itself runs in the ``Reviewer eval`` GitHub Action (durable runner,
isolated from the serving deployment). The Action's harness reports progress
into a LangGraph store record (namespace ``["evals"]``, key ``"reviewer"``) via
``evals.reviewer.store_reporter``; this module reads that record for the
dashboard and reconciles a run whose heartbeat has gone stale (e.g. the Action
was killed) to ``failed``.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any, cast

from langgraph_sdk import get_client

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

logger = logging.getLogger(__name__)


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_langgraph_url() -> str | None:
    return os.environ.get("LANGGRAPH_URL") or os.environ.get("LANGGRAPH_URL_PROD")


def _eval_project() -> str:
    return os.environ.get("EVAL_LANGSMITH_PROJECT") or DEFAULT_EVAL_PROJECT


def _resolve_eval_config() -> ReviewerEvalConfig:
    return resolve_config(
        {
            "langsmith_project": _eval_project(),
            "langgraph_url": _resolve_langgraph_url() or "",
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
        "updated_at": _now_iso(),
    }


async def _get_record() -> ReviewerEvalRecord | None:
    try:
        item = await _client().store.get_item(EVALS_NAMESPACE, REVIEWER_EVAL_KEY)
    except Exception as e:
        logger.debug("store get_item failed for reviewer eval: %s", e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return cast(ReviewerEvalRecord, value) if isinstance(value, dict) else None


async def _put_record(record: ReviewerEvalRecord) -> ReviewerEvalRecord:
    stamped = record.copy()
    stamped["updated_at"] = _now_iso()
    try:
        await _client().store.put_item(EVALS_NAMESPACE, REVIEWER_EVAL_KEY, dict(stamped))
    except Exception:
        logger.exception("Failed to persist reviewer eval status")
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


async def get_reviewer_eval_status() -> dict[str, Any]:
    """Return the latest reviewer-eval status, reconciling a stale ``running``.

    The GitHub Action refreshes the record's heartbeat while it runs. A poll
    only marks the run failed once the heartbeat is stale, so a healthy run is
    left untouched and a killed Action surfaces as ``failed`` within the stale
    threshold.
    """
    record = await _get_record()
    if record is None:
        return dict(_idle_record())
    if record.get("status") != "running" or _is_heartbeat_fresh(record):
        return dict(record)
    stale = record.copy()
    stale["status"] = "failed"
    stale["finished_at"] = record.get("finished_at") or _now_iso()
    stale["error"] = "Eval process is no longer tracked (GitHub Action stopped?)."
    return dict(await _put_record(stale))
