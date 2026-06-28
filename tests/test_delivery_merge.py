from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_merge as merge_worker
from agent import delivery_queue as queue
from agent.merge_controller import MergeResult


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


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


class _MergeRecorder:
    def __init__(self, result: MergeResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> MergeResult:
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    return client


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


async def _mergeable_item(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": "sports-cms",
        "provider": "linear",
        "external_work_item_id": "ENG-123",
        "title": "Fix teaser card",
        "status": "review",
        "worker_thread_id": "worker-1",
        "reviewer_thread_id": "reviewer-1",
        "qa_thread_id": "qa-1",
        "reviewed_sha": "head-sha",
        "credential_identity": "github:user:octocat",
        "merge_credential_identity": "github:user:octocat",
        "merge_policy": {
            "enabled": True,
            "strategy": "squash",
            "target_branch": "main",
            "required_checks": ["tests"],
        },
        "repo": {"owner": "example", "name": "sports-cms"},
        "pr": {
            "number": 42,
            "state": "open",
            "draft": False,
            "head": {"sha": "head-sha"},
            "base": {"ref": "main"},
        },
        "qa_evidence": {
            "complete": True,
            "gates": [
                {"name": "unit", "passed": True},
                {"name": "browser", "passed": True},
            ],
        },
        "gate_policy": {"blocking_gates": ["unit", "browser"]},
        "findings": [],
        "required_checks": [{"name": "tests", "status": "completed", "conclusion": "success"}],
        "review_result": {"qa_result": {"passed": True}},
    }
    payload.update(overrides)
    return await queue.upsert_delivery_queue_item(payload, preflight=_ready_preflight())


async def test_execute_delivery_merge_records_success(fake_client: _FakeClient) -> None:
    item = await _mergeable_item()
    recorder = _MergeRecorder(MergeResult(True, "merged", "merged", sha="merge-sha"))

    updated = await merge_worker.execute_delivery_merge(
        item["id"],
        token="token",
        merge_func=recorder,
    )

    assert updated["status"] == "done"
    assert updated["status_reason"] == "merge completed"
    assert updated["merge_status"] == "merged"
    assert updated["merge_commit_sha"] == "merge-sha"
    assert updated["merge_strategy"] == "squash"
    assert updated["target_branch"] == "main"
    assert updated["merge_credential_identity"] == "github:user:octocat"
    assert updated["merge_worker_thread_id"].startswith("delivery-merge-")
    assert updated["merge_audit"]["decision"]["allowed"] is True
    assert recorder.calls[0]["owner"] == "example"
    assert recorder.calls[0]["repo"] == "sports-cms"
    assert recorder.calls[0]["pr_number"] == 42


async def test_execute_delivery_merge_refuses_stale_head_after_verification(
    fake_client: _FakeClient,
) -> None:
    item = await _mergeable_item()
    recorder = _MergeRecorder(MergeResult(True, "merged", "merged", sha="merge-sha"))

    updated = await merge_worker.execute_delivery_merge(
        item["id"],
        token="token",
        pr={
            **item["pr"],
            "head": {"sha": "new-head-sha"},
            "base": {"ref": "main"},
        },
        merge_func=recorder,
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "head_sha_mismatch"
    assert updated["merge_status"] == "blocked"
    assert recorder.calls == []


async def test_execute_delivery_merge_blocks_conflict(fake_client: _FakeClient) -> None:
    item = await _mergeable_item()
    recorder = _MergeRecorder(
        MergeResult(
            False,
            "blocked",
            "github_merge_blocked",
            sha="head-sha",
            http_status=409,
            details={"message": "Pull Request is not mergeable"},
        )
    )

    updated = await merge_worker.execute_delivery_merge(
        item["id"],
        token="token",
        merge_func=recorder,
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "github_merge_blocked"
    assert updated["merge_audit"]["provider"]["http_status"] == 409


async def test_execute_delivery_merge_fails_provider_error(fake_client: _FakeClient) -> None:
    item = await _mergeable_item()
    recorder = _MergeRecorder(
        MergeResult(
            False,
            "error",
            "github_merge_failed",
            sha="head-sha",
            http_status=500,
            details={"message": "provider unavailable"},
        )
    )

    updated = await merge_worker.execute_delivery_merge(
        item["id"],
        token="token",
        merge_func=recorder,
    )

    assert updated["status"] == "failed"
    assert updated["status_reason"] == "github_merge_failed"
    assert updated["merge_status"] == "error"
    assert updated["merge_audit"]["provider"]["details"] == {"message": "provider unavailable"}


async def test_execute_delivery_merge_blocks_disabled_policy(fake_client: _FakeClient) -> None:
    item = await _mergeable_item(
        merge_policy={
            "enabled": False,
            "strategy": "squash",
            "target_branch": "main",
        }
    )
    recorder = _MergeRecorder(MergeResult(True, "merged", "merged", sha="merge-sha"))

    updated = await merge_worker.execute_delivery_merge(
        item["id"],
        token="token",
        merge_func=recorder,
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "merge_policy_disabled"
    assert recorder.calls == []


async def test_execute_delivery_merge_blocks_target_branch_mismatch(
    fake_client: _FakeClient,
) -> None:
    item = await _mergeable_item(
        pr={
            "number": 42,
            "state": "open",
            "draft": False,
            "head": {"sha": "head-sha"},
            "base": {"ref": "develop"},
        }
    )
    recorder = _MergeRecorder(MergeResult(True, "merged", "merged", sha="merge-sha"))

    updated = await merge_worker.execute_delivery_merge(
        item["id"],
        token="token",
        merge_func=recorder,
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "target_branch_mismatch"
    assert recorder.calls == []
