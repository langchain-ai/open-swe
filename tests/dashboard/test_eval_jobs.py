"""Both ends of the reviewer-eval store record agree on ``ReviewerEvalRecord``."""

import pytest

from agent.review.eval_config import resolve_config
from agent.review.eval_jobs import _idle_record, _is_heartbeat_fresh
from agent.review.eval_store import HEARTBEAT_STALE_SECONDS, ReviewerEvalRecord
from evals.reviewer.store_reporter import StoreReporter

RECORD_FIELDS = set(ReviewerEvalRecord.__annotations__)


def _reporter(monkeypatch: pytest.MonkeyPatch) -> StoreReporter:
    monkeypatch.setenv("LANGGRAPH_URL", "http://localhost:2024")
    for var in ("GITHUB_ACTOR", "GITHUB_RUN_ID", "GITHUB_SERVER_URL", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(var, raising=False)
    return StoreReporter(
        config=resolve_config({"experiment_prefix": "run-x", "langsmith_project": "proj-x"}),
        limit=3,
        total=7,
        created_by="octocat",
        completed_getter=lambda: 2,
        tail_getter=lambda: "tail output",
        experiment_url_getter=lambda: "https://smith.langchain.com/exp",
    )


def test_reporter_writes_every_field_the_dashboard_reads(monkeypatch: pytest.MonkeyPatch):
    record = _reporter(monkeypatch)._record(status="running")
    assert set(record) == RECORD_FIELDS
    assert record["name"] == "reviewer"
    assert record["status"] == "running"
    assert record["run_name"] == "run-x"
    assert record["langsmith_project"] == "proj-x"
    assert record["limit"] == 3
    assert record["progress"] == {"completed": 2, "total": 7}
    assert record["log_tail"] == "tail output"
    assert record["experiment_url"] == "https://smith.langchain.com/exp"
    assert record["created_by"] == "octocat"
    assert record["trigger"] == "github_action"
    assert record["config_snapshot"]["cap"] == 6
    assert record["finished_at"] is None
    assert record["error"] is None


def test_reporter_marks_a_finished_run(monkeypatch: pytest.MonkeyPatch):
    record = _reporter(monkeypatch)._record(
        status="failed", finished_at="2026-01-01T00:00:00+00:00", error="RuntimeError: boom"
    )
    assert record["status"] == "failed"
    assert record["finished_at"] == "2026-01-01T00:00:00+00:00"
    assert record["error"] == "RuntimeError: boom"


def test_idle_record_matches_the_shared_contract(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EVAL_LANGSMITH_PROJECT", raising=False)
    monkeypatch.setenv("LANGGRAPH_URL", "https://deployment.example")
    record = _idle_record()
    assert set(record) == RECORD_FIELDS
    assert record["status"] == "idle"
    assert record["run_name"] == "openswe-review-confidence"
    assert record["langsmith_project"] == "open-swe-evals"
    assert record["config_snapshot"]["langgraph_url"] == "https://deployment.example"
    assert record["heartbeat"] is None


def test_heartbeat_freshness_uses_the_shared_threshold(monkeypatch: pytest.MonkeyPatch):
    record = _reporter(monkeypatch)._record(status="running")
    assert _is_heartbeat_fresh(record) is True

    stale = record.copy()
    stale["heartbeat"] = "2026-01-01T00:00:00+00:00"
    assert _is_heartbeat_fresh(stale) is False

    missing = record.copy()
    missing["heartbeat"] = None
    assert _is_heartbeat_fresh(missing) is False
    assert HEARTBEAT_STALE_SECONDS == 60
