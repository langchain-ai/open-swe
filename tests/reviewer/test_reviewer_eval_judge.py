from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

from langsmith.schemas import Example, Run

from evals.reviewer import judge


def _result(match: bool, confidence: float) -> judge.PairResult:
    return {"match": match, "confidence": confidence, "reasoning": "reason"}


def test_select_pairs_maximizes_cardinality_before_confidence() -> None:
    matrix = [
        [_result(True, 0.9), _result(True, 0.8)],
        [_result(True, 0.7), _result(False, 0.0)],
    ]

    assert set(judge._select_pairs(matrix)) == {(0, 1), (1, 0)}


def test_select_pairs_uses_confidence_to_break_cardinality_ties() -> None:
    matrix = [
        [_result(True, 0.9), _result(True, 0.1)],
        [_result(True, 0.2), _result(True, 0.8)],
    ]

    assert set(judge._select_pairs(matrix)) == {(0, 0), (1, 1)}


def test_recall_at_cap_never_exceeds_one() -> None:
    recall_at_cap, ceiling = judge._recall_at_cap(tp=7, golden_count=7, cap=6)

    assert recall_at_cap == 1.0
    assert ceiling == 6 / 7


def test_judge_match_deduplicates_and_persists_full_matrix() -> None:
    run = SimpleNamespace(
        outputs={
            "comments": [
                {"file": "a.py", "line": 1, "body": "same", "severity": "high"},
                {"file": "a.py", "line": 1, "body": " same ", "severity": "high"},
                {"file": "b.py", "line": 2, "body": "other", "severity": "medium"},
            ]
        }
    )
    example = SimpleNamespace(
        id=uuid4(),
        inputs={"repo": "acme/repo"},
        outputs={
            "golden_comments": [
                {"comment": "first", "severity": "High"},
                {"comment": "second", "severity": "Medium"},
            ]
        },
    )
    calls: list[tuple[str, str]] = []

    def _pair(golden: judge.ReviewComment, candidate: judge.ReviewComment) -> judge.PairResult:
        calls.append((golden.get("comment", ""), candidate.get("body", "")))
        return _result(golden.get("comment") == "first" and candidate.get("file") == "a.py", 0.8)

    with patch("evals.reviewer.judge._judge_pair", side_effect=_pair):
        result = judge.judge_match(cast(Run, run), cast(Example, example))

    by_key = {item["key"]: item for item in result["results"]}
    assert len(calls) == 4
    assert by_key["n_candidates_raw"]["score"] == 3
    assert by_key["n_candidates"]["score"] == 2
    assert by_key["n_duplicates"]["score"] == 1
    matrix = json.loads(by_key["pairwise_match_matrix"]["value"])
    assert len(matrix["cells"]) == 4
    assert matrix["selected_pairs"] == [{"candidate_index": 0, "golden_index": 0}]


def test_judge_pair_normalizes_malformed_response() -> None:
    model = MagicMock()
    model.invoke.return_value.content = '{"match": "yes", "confidence": 4, "reasoning": 7}'

    with patch("evals.reviewer.judge._get_judge", return_value=model):
        result = judge._judge_pair({"comment": "gold"}, {"body": "candidate"})

    assert result == {"match": False, "confidence": 1.0, "reasoning": ""}


def test_aggregate_pr_reports_synthetic_and_medium_plus_metrics() -> None:
    judge._drain_counts()
    base = {
        "tp": 1,
        "fp": 1,
        "fn": 0,
        "precision": 0.5,
        "recall": 1.0,
        "f1": 2 / 3,
        "medium_plus_tp": 1,
        "medium_plus_fp": 0,
        "medium_plus_fn": 0,
        "medium_plus_precision": 1.0,
        "medium_plus_recall": 1.0,
        "medium_plus_f1": 1.0,
    }
    judge._record_counts(uuid4(), cast(judge.ExampleCounts, {**base, "is_synthetic": False}))
    judge._record_counts(uuid4(), cast(judge.ExampleCounts, {**base, "is_synthetic": True}))

    result = judge.aggregate_pr([], [])

    keys = {item["key"] for item in result["results"]}
    assert "micro_f1" in keys
    assert "medium_plus_micro_f1" in keys
    assert "synthetic_micro_f1" in keys
    assert "upstream_micro_f1" in keys
