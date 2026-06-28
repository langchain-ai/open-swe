from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_queue as queue
from agent import delivery_review as review


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def search_items(
        self,
        namespace: list[str],
        filter: dict[str, Any] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}


class _FakeThreads:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {}

    async def create(
        self,
        *,
        thread_id: str,
        metadata: dict[str, Any],
        if_exists: str = "raise",
    ) -> dict[str, Any]:
        self.metadata.setdefault(thread_id, dict(metadata))
        return {"thread_id": thread_id, "metadata": self.metadata[thread_id]}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.metadata.setdefault(thread_id, {}).update(metadata)


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.threads = _FakeThreads()


class _DispatchRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        thread_id: str,
        content: str,
        configurable: dict[str, Any],
        *,
        source: str,
        assistant_id: str = "agent",
        metadata: dict[str, Any] | None = None,
        client: Any = None,
    ) -> dict[str, Any]:
        run_id = f"run-{len(self.calls) + 1}"
        self.calls.append(
            {
                "thread_id": thread_id,
                "content": content,
                "configurable": configurable,
                "source": source,
                "assistant_id": assistant_id,
                "metadata": metadata,
                "client": client,
                "run_id": run_id,
            }
        )
        return {"run_id": run_id}


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    return client


@pytest.fixture
def dispatch_recorder(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    recorder = _DispatchRecorder()
    monkeypatch.setattr(review, "dispatch_agent_run", recorder)
    return recorder


def _ready_preflight() -> queue.PreflightInput:
    return {
        "active_project": True,
        "readiness": True,
        "issue_context": True,
        "credentials": True,
        "ai_hub_ready": True,
        "sandbox_profile": True,
        "budget": True,
        "duplicate_active_run": False,
        "kill_switch": False,
    }


async def _reviewable_item(**overrides: Any) -> dict[str, Any]:
    record = await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": "ENG-123",
            "title": "Fix teaser card",
            "status": "review",
            "worker_thread_id": "worker-1",
            "gate_policy": {"qa_evidence": True, "blocking_gates": ["unit", "browser"]},
            "qa_evidence": {
                "complete": True,
                "browser_relevant": True,
                "preview_url": "https://preview.example.test/teaser",
                "screenshots": ["https://artifacts.example.test/teaser.png"],
                "traces": ["https://artifacts.example.test/trace.zip"],
            },
            "worker_result": {"cause": "Teaser variant missed modifier."},
            "pr": {
                "number": 42,
                "url": "https://github.com/example/sports-cms/pull/42",
                "head": {"sha": "head-sha"},
            },
        },
        preflight=_ready_preflight(),
    )
    if overrides:
        record = await queue.transition_delivery_queue_status(
            record["id"],
            record["status"],
            extra=overrides,
        )
    return record


async def test_launch_review_checks_uses_distinct_reviewer_and_required_qa_thread(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    item = await _reviewable_item()

    result = await review.launch_delivery_review_checks(item["id"], client=fake_client)

    assert result["status"] == "launched"
    assert result["reviewer_thread_id"] != "worker-1"
    assert result["qa_thread_id"] != "worker-1"
    assert len(dispatch_recorder.calls) == 2
    assert dispatch_recorder.calls[0]["assistant_id"] == "reviewer"
    assert dispatch_recorder.calls[1]["assistant_id"] == "agent"
    assert dispatch_recorder.calls[0]["configurable"]["qa_required"] is True
    updated = await queue.read_delivery_queue_item(item["id"])
    assert updated["review_result"]["status"] == "pending"
    assert updated["reviewer_thread_id"] == result["reviewer_thread_id"]
    assert updated["qa_thread_id"] == result["qa_thread_id"]
    assert updated["merge_eligible"] is False


async def test_launch_review_checks_refuses_self_review(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    item = await _reviewable_item(reviewer_thread_id="worker-1")

    result = await review.launch_delivery_review_checks(item["id"], client=fake_client)

    assert result["status"] == "refused"
    assert result["reason"] == "self_review_refused"
    assert dispatch_recorder.calls == []
    updated = await queue.read_delivery_queue_item(item["id"])
    assert updated["status"] == "blocked"
    assert updated["blocker_reason"] == "self_review_refused"


async def test_record_review_blocks_missing_required_qa_check(
    fake_client: _FakeClient,
) -> None:
    item = await _reviewable_item(reviewer_thread_id="reviewer-1", qa_thread_id="qa-1")

    updated = await review.record_delivery_review_result(
        item["id"],
        {"reviewed_sha": "head-sha", "findings": []},
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "qa_check_missing"
    assert updated["merge_eligible"] is False
    assert updated["review_result"]["blockers"] == [
        {"code": "qa_check_missing", "message": "Required QA check is missing."}
    ]


async def test_record_review_blocks_unresolved_blocking_finding(
    fake_client: _FakeClient,
) -> None:
    item = await _reviewable_item(reviewer_thread_id="reviewer-1", qa_thread_id="qa-1")

    updated = await review.record_delivery_review_result(
        item["id"],
        {
            "reviewed_sha": "head-sha",
            "qa_result": {"passed": True},
            "findings": [{"id": "finding-1", "status": "open", "blocking": True}],
        },
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "unresolved_blocking_finding:finding-1"
    assert updated["merge_eligible"] is False
    assert updated["review_result"]["blocking"] is True


async def test_record_review_allows_passing_review(fake_client: _FakeClient) -> None:
    item = await _reviewable_item(reviewer_thread_id="reviewer-1", qa_thread_id="qa-1")

    updated = await review.record_delivery_review_result(
        item["id"],
        {
            "reviewed_sha": "head-sha",
            "qa_result": {"passed": True, "artifacts": ["qa-report.json"]},
            "findings": [{"id": "nit-1", "status": "open", "severity": "low"}],
        },
    )

    assert updated["status"] == "review"
    assert updated["status_reason"] == "review passed"
    assert updated["merge_eligible"] is True
    assert updated["reviewed_sha"] == "head-sha"
    assert updated["review_result"]["status"] == "passed"
    assert updated["review_result"]["evidence_snapshot"] == item["qa_evidence"]
    assert updated["delivery"]["reviewed_sha"] == "head-sha"
