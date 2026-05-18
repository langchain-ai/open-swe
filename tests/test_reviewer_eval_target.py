from __future__ import annotations

from typing import Any

import pytest

from agent.reviewer_findings import new_finding
from evals.reviewer import target


def test_eval_target_marks_runs_as_eval_dry_run() -> None:
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


@pytest.mark.asyncio
async def test_extract_surfaced_comments_uses_publish_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    high = new_finding(
        severity="high",
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
