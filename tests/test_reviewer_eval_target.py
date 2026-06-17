from __future__ import annotations

from typing import Any

import pytest

from agent.reviewer_findings import new_finding
from evals.reviewer import target


def test_eval_target_marks_runs_as_eval_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVIEWER_EVAL_MODEL_ID", raising=False)
    monkeypatch.delenv("REVIEWER_EVAL_REASONING_EFFORT", raising=False)
    configurable = target._build_configurable(
        {
            "repo": "acme/repo",
            "pr_number": 1,
            "pr_url": "https://github.com/acme/repo/pull/1",
            "base_sha": "base",
            "head_sha": "head",
            "head_ref": "branch",
        }
    )

    assert configurable["reviewer_eval"] is True
    assert configurable["eval"] is True
    assert configurable["__is_for_execution__"] is True
    assert "source" not in configurable


def test_eval_target_passes_model_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEWER_EVAL_MODEL_ID", "anthropic:claude-opus-4-8")
    monkeypatch.setenv("REVIEWER_EVAL_REASONING_EFFORT", "high")

    configurable = target._build_configurable(
        {
            "repo": "acme/repo",
            "pr_number": 1,
            "pr_url": "https://github.com/acme/repo/pull/1",
            "base_sha": "base",
            "head_sha": "head",
            "head_ref": "branch",
        }
    )

    assert configurable["reviewer_model_id"] == "anthropic:claude-opus-4-8"
    assert configurable["reviewer_reasoning_effort"] == "high"


def _result_with_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {"messages": [{"tool_calls": [{"name": "add_finding", "args": f} for f in findings]}]}


def test_extract_comments_includes_all_confidences() -> None:
    result = _result_with_findings(
        [
            {
                "file": "a.py",
                "severity": "high",
                "confidence": "low",
                "description": "lo",
                "start_line": 1,
                "end_line": 1,
            },
            {
                "file": "b.py",
                "severity": "high",
                "confidence": "high",
                "description": "hi",
                "start_line": 2,
                "end_line": 2,
            },
        ]
    )
    comments = target._extract_comments(result)
    assert {c["file"] for c in comments} == {"a.py", "b.py"}


@pytest.mark.asyncio
async def test_extract_surfaced_comments_uses_publish_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high = new_finding(
        severity="high",
        confidence="high",
        category="correctness",
        file="a.py",
        start_line=10,
        end_line=10,
        description="high signal",
        sha="head",
        finding_id="f_high",
    )
    low = new_finding(
        severity="low",
        confidence="high",
        category="style",
        file="b.py",
        start_line=20,
        end_line=20,
        description="low signal",
        sha="head",
        finding_id="f_low",
    )

    class Threads:
        async def get(self, _thread_id: str) -> dict[str, Any]:
            return {"metadata": {"findings": [high, low]}}

    class Client:
        threads = Threads()

    monkeypatch.setenv("REVIEWER_EVAL_SEVERITY_THRESHOLD", "medium")
    monkeypatch.setenv("REVIEWER_EVAL_CAP", "4")

    comments = await target._extract_surfaced_comments(Client(), "tid")

    assert comments == [
        {
            "file": "a.py",
            "line": 10,
            "body": "high signal",
            "severity": "high",
        }
    ]


def test_completed_counter_increments() -> None:
    start = target.get_completed_count()
    target._record_completed()
    target._record_completed()
    assert target.get_completed_count() == start + 2
