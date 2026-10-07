from typing import Any, cast

from openswe.review.findings import Finding
from openswe.utils import reviewer_outcomes
from openswe.utils.reviewer_outcomes import (
    FALSE_POSITIVE,
    TRUE_POSITIVE,
    upsert_finding_outcome,
)


class _FakeDataset:
    def __init__(self, dataset_id: str, name: str) -> None:
        self.id = dataset_id
        self.name = name


class _FakeClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.conflict_once = False

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def list_datasets(self, dataset_name: str):  # noqa: ANN001
        yield _FakeDataset("ds_wrong", "other-dataset")
        yield _FakeDataset("ds_123", dataset_name)

    async def create_example(self, **kwargs: Any) -> None:
        if self.conflict_once:
            self.conflict_once = False
            raise RuntimeError("already exists")
        self.created.append(kwargs)

    def update_example(self, **kwargs: Any) -> None:
        self.updated.append(kwargs)


def _patch_client(monkeypatch, fake: _FakeClient | None) -> None:  # noqa: ANN001
    if fake is None:
        monkeypatch.setattr(reviewer_outcomes, "_outcomes_credentials", lambda: None)
        return
    monkeypatch.setattr(reviewer_outcomes, "_outcomes_credentials", lambda: ("k", "https://api"))
    monkeypatch.setattr(reviewer_outcomes, "async_langsmith_client", lambda *a: fake)
    monkeypatch.setattr(reviewer_outcomes, "sync_langsmith_client", lambda *a: cast(Any, fake))


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


async def test_upsert_finding_outcome_builds_payload(monkeypatch) -> None:  # noqa: ANN001
    fake = _FakeClient()
    _patch_client(monkeypatch, fake)

    ok = await upsert_finding_outcome(
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


async def test_upsert_finding_outcome_updates_on_conflict(monkeypatch) -> None:  # noqa: ANN001
    fake = _FakeClient()
    fake.conflict_once = True
    _patch_client(monkeypatch, fake)

    ok = await upsert_finding_outcome(
        _finding(), label=FALSE_POSITIVE, label_source="dismissed", repo="o/r"
    )
    assert ok
    assert not fake.created
    assert len(fake.updated) == 1
