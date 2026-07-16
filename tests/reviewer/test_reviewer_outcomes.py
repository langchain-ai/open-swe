from __future__ import annotations

from typing import Any, cast

from agent.review.findings import Finding
from agent.utils import reviewer_outcomes
from agent.utils.reviewer_outcomes import (
    FALSE_POSITIVE,
    TRUE_POSITIVE,
    emit_finding_status_outcome,
    outcome_from_score,
    outcome_from_status,
    upsert_finding_outcome,
)


def test_outcome_from_status_resolved_by_commit() -> None:
    assert outcome_from_status("resolved", first_seen_sha="aaa", head_sha="bbb") == (
        TRUE_POSITIVE,
        "resolved_by_commit",
    )


def test_outcome_from_status_resolved_same_sha() -> None:
    assert outcome_from_status("resolved", first_seen_sha="aaa", head_sha="aaa") == (
        TRUE_POSITIVE,
        "resolved_same_sha",
    )


def test_outcome_from_status_dismissed() -> None:
    assert outcome_from_status("dismissed", first_seen_sha="aaa", head_sha="bbb") == (
        FALSE_POSITIVE,
        "dismissed",
    )


def test_outcome_from_status_open_is_none() -> None:
    assert outcome_from_status("open", first_seen_sha="aaa", head_sha="bbb") is None


def test_outcome_from_score() -> None:
    assert outcome_from_score(1.0, source="github") == (TRUE_POSITIVE, "github_thumbs_up")
    assert outcome_from_score(0.0, source="github") == (FALSE_POSITIVE, "github_thumbs_down")
    assert outcome_from_score(1.0, source="slack") == (TRUE_POSITIVE, "slack_thumbs_up")
    assert outcome_from_score(None, source="github") is None


def test_example_id_is_deterministic() -> None:
    a = reviewer_outcomes._example_id("o/r", "f_1", "dismissed")
    b = reviewer_outcomes._example_id("o/r", "f_1", "dismissed")
    c = reviewer_outcomes._example_id("o/r", "f_1", "resolved_by_commit")
    assert a == b
    assert a != c


class _FakeDataset:
    id = "ds_123"


class _FakeClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.conflict_once = False

    def list_datasets(self, dataset_name: str):  # noqa: ANN001
        return iter([_FakeDataset()])

    def create_example(self, **kwargs: Any) -> None:
        if self.conflict_once:
            self.conflict_once = False
            raise RuntimeError("already exists")
        self.created.append(kwargs)

    def update_example(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)


def _finding() -> Finding:
    return cast(
        Finding,
        {
            "id": "f_abc",
            "file": "app/x.rb",
            "start_line": 10,
            "end_line": 12,
            "side": "RIGHT",
            "diff_hunk": "@@ -1 +1 @@\n-foo\n+bar",
            "title": "NoMethodError on nil",
            "description": "body",
            "severity": "high",
            "confidence": "high",
            "category": "correctness",
            "first_seen_sha": "aaa",
            "github_review_run_id": "run_1",
            "resolution_note": "fixed in def",
        },
    )


def test_upsert_finding_outcome_builds_payload(monkeypatch) -> None:  # noqa: ANN001
    fake = _FakeClient()
    monkeypatch.setattr(reviewer_outcomes, "_outcomes_client", lambda: fake)

    ok = upsert_finding_outcome(
        _finding(),
        label=TRUE_POSITIVE,
        label_source="resolved_by_commit",
        repo="o/r",
        pr_number=7,
        pr_url="https://github.com/o/r/pull/7",
        base_sha="aaa",
        head_sha="bbb",
        thread_id="t1",
    )
    assert ok
    assert len(fake.created) == 1
    call = fake.created[0]
    assert call["dataset_id"] == "ds_123"
    assert call["inputs"]["repo"] == "o/r"
    assert call["inputs"]["file"] == "app/x.rb"
    assert call["inputs"]["diff_hunk"].startswith("@@")
    assert call["outputs"]["label"] == TRUE_POSITIVE
    assert call["outputs"]["label_source"] == "resolved_by_commit"
    assert call["outputs"]["finding"]["severity"] == "high"
    assert call["metadata"]["granularity"] == "finding"
    assert call["metadata"]["repo"] == "o/r"
    assert call["metadata"]["run_id"] == "run_1"


def test_upsert_finding_outcome_updates_on_conflict(monkeypatch) -> None:  # noqa: ANN001
    fake = _FakeClient()
    fake.conflict_once = True
    monkeypatch.setattr(reviewer_outcomes, "_outcomes_client", lambda: fake)

    ok = upsert_finding_outcome(
        _finding(), label=FALSE_POSITIVE, label_source="dismissed", repo="o/r"
    )
    assert ok
    assert not fake.created
    assert len(fake.updated) == 1


def test_upsert_no_client_is_noop(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(reviewer_outcomes, "_outcomes_client", lambda: None)
    assert (
        upsert_finding_outcome(_finding(), label=TRUE_POSITIVE, label_source="x", repo="o/r")
        is False
    )


def test_emit_finding_status_outcome_maps_and_calls(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, Any] = {}

    def fake_upsert(finding, **kwargs: Any) -> bool:  # noqa: ANN001
        captured.update(kwargs)
        captured["finding_id"] = finding["id"]
        return True

    monkeypatch.setattr(reviewer_outcomes, "upsert_finding_outcome", fake_upsert)

    configurable = {
        "repo": {"owner": "o", "name": "r"},
        "pr_number": 7,
        "pr_url": "https://github.com/o/r/pull/7",
        "base_sha": "aaa",
        "head_sha": "bbb",
    }
    assert emit_finding_status_outcome(
        _finding(), "resolved", configurable=configurable, thread_id="t1"
    )
    assert captured["repo"] == "o/r"
    assert captured["label"] == TRUE_POSITIVE
    assert captured["label_source"] == "resolved_by_commit"
    assert captured["pr_number"] == 7


def test_emit_finding_status_outcome_no_repo_is_noop(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(reviewer_outcomes, "upsert_finding_outcome", lambda *a, **k: pytest_fail())
    assert emit_finding_status_outcome(_finding(), "dismissed", configurable={}) is False


def pytest_fail() -> bool:
    raise AssertionError("upsert should not be called without a repo")
