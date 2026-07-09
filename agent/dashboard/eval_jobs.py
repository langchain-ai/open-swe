"""Track the reviewer eval for the admin dashboard.

The eval itself runs in the ``Reviewer eval`` GitHub Action (durable runner,
isolated from the serving deployment). The Action's harness reports progress
into a LangGraph store record (namespace ``["evals"]``, key ``"reviewer"``) via
``evals.reviewer.store_reporter``; this module reads that record for the
dashboard and reconciles a run whose heartbeat has gone stale (e.g. the Action
was killed) to ``failed``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph_sdk import get_client

from agent.reviewer_eval_store import (
    _HEARTBEAT_STALE_SECONDS,
    DEFAULT_EVAL_PROJECT,
    EVALS_NAMESPACE,
    REVIEWER_EVAL_KEY,
)
from agent.reviewer_findings import REVIEW_FINDING_CAP

logger = logging.getLogger(__name__)

EvalStatus = Literal["idle", "running", "completed", "failed"]
ScoreMode = Literal["all_findings", "surfaced_findings"]
Severity = Literal["low", "medium", "high", "critical"]


class ReviewerEvalConfig(TypedDict):
    dataset_name: str
    experiment_prefix: str
    max_concurrency: int
    langsmith_project: str
    langgraph_url: str
    assistant_id: str
    model_id: str
    reasoning_effort: str
    score_mode: ScoreMode
    severity_threshold: Severity
    cap: int


DEFAULT_REVIEWER_EVAL_CONFIG: ReviewerEvalConfig = {
    "dataset_name": "openswe-reviewer-v1",
    "experiment_prefix": "openswe-review-confidence",
    "max_concurrency": 5,
    "langsmith_project": DEFAULT_EVAL_PROJECT,
    "langgraph_url": "",
    "assistant_id": "reviewer",
    "model_id": "google_genai:gemini-3.5-flash",
    "reasoning_effort": "medium",
    "score_mode": "surfaced_findings",
    "severity_threshold": "low",
    "cap": REVIEW_FINDING_CAP,
}


def _client():
    return get_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _resolve_langgraph_url() -> str | None:
    return os.environ.get("LANGGRAPH_URL") or os.environ.get("LANGGRAPH_URL_PROD")


def _eval_project() -> str:
    return os.environ.get("EVAL_LANGSMITH_PROJECT") or DEFAULT_EVAL_PROJECT


def _resolve_eval_config(config: ReviewerEvalConfig | None = None) -> ReviewerEvalConfig:
    resolved: ReviewerEvalConfig = {
        **DEFAULT_REVIEWER_EVAL_CONFIG,
        "langsmith_project": _eval_project(),
        "langgraph_url": _resolve_langgraph_url() or "",
    }
    if config is not None:
        resolved.update(config)
    return resolved


def _idle_record() -> dict[str, Any]:
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


async def _get_record() -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(EVALS_NAMESPACE, REVIEWER_EVAL_KEY)
    except Exception as e:
        logger.debug("store get_item failed for reviewer eval: %s", e)
        return None
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _put_record(record: dict[str, Any]) -> dict[str, Any]:
    record = {**record, "updated_at": _now_iso()}
    try:
        await _client().store.put_item(EVALS_NAMESPACE, REVIEWER_EVAL_KEY, record)
    except Exception:
        logger.exception("Failed to persist reviewer eval status")
    return record


def _heartbeat_age_seconds(record: dict[str, Any]) -> float | None:
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


def _is_heartbeat_fresh(record: dict[str, Any]) -> bool:
    age = _heartbeat_age_seconds(record)
    return age is not None and age <= _HEARTBEAT_STALE_SECONDS


async def get_reviewer_eval_status() -> dict[str, Any]:
    """Return the latest reviewer-eval status, reconciling a stale ``running``.

    The GitHub Action refreshes the record's heartbeat while it runs. A poll
    only marks the run failed once the heartbeat is stale, so a healthy run is
    left untouched and a killed Action surfaces as ``failed`` within the stale
    threshold.
    """
    record = await _get_record()
    if record is None:
        return _idle_record()
    if record.get("status") != "running":
        return record
    if _is_heartbeat_fresh(record):
        return record
    return await _put_record(
        {
            **record,
            "status": "failed",
            "finished_at": record.get("finished_at") or _now_iso(),
            "error": "Eval process is no longer tracked (GitHub Action stopped?).",
        }
    )
