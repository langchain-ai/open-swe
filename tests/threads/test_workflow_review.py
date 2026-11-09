from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.tasks.store import TaskMembership
from openswe.threads import workflow_review
from openswe.threads.workflow_review import WorkflowReview


@pytest.mark.parametrize(
    ("status", "mismatch", "expected"),
    [
        ("approved", None, "approved"),
        ("approved", "retried", "approved"),
        ("rejected", None, "rejected"),
        ("pending", None, "pending"),
        ("approved", "owner", "pending"),
        ("approved", "workspace", "pending"),
        ("approved", "private", "pending"),
        ("approved", "branch", "pending"),
        ("approved", "repo", "pending"),
        ("approved", "fingerprint", "pending"),
        ("approved", "standalone", "pending"),
        ("approved", "older", "pending"),
    ],
)
async def test_worker_review_reflects_only_readable_coordinator_decision(
    monkeypatch: pytest.MonkeyPatch, status: str, mismatch: str | None, expected: str
) -> None:
    record = {
        "status": "pending",
        "repo": "langchain-ai/example",
        "branch": "workflow-change",
        "requested_at": "2026-11-08T14:00:00Z",
    }
    metadata = {
        "owner_login": "alice",
        "workspace": "workspace",
        "workflow_push_approvals": {"fp": record},
    }
    if mismatch == "retried":
        record["first_requested_at"] = record["requested_at"]
        record["requested_at"] = "2026-11-08T16:00:00Z"
    original = deepcopy(metadata)
    decision = {
        **record,
        "status": status,
        "decided_by": "alice",
        "decided_at": "2026-11-08T15:00:00Z",
    }
    coordinator = {
        **metadata,
        "workflow_push_approvals": {"fp": decision},
    }
    if mismatch in {"branch", "repo"}:
        decision[mismatch] = "different"
    if mismatch == "older":
        decision["decided_at"] = "2026-11-08T13:00:00Z"
    if mismatch == "fingerprint":
        coordinator["workflow_push_approvals"] = {"different": decision}
    if mismatch == "owner":
        coordinator["owner_login"] = "bob"
    if mismatch == "workspace":
        coordinator["workspace"] = "other"
    if mismatch == "private":
        coordinator["visibility"] = "private"
    context = SimpleNamespace(
        membership=SimpleNamespace(role="worker"),
        task=SimpleNamespace(coordinator_thread_id="parent"),
    )
    monkeypatch.setattr(
        TaskMembership,
        "context_for_thread",
        AsyncMock(return_value=None if mismatch == "standalone" else context),
    )
    monkeypatch.setattr(
        workflow_review.THREADS,
        "get",
        AsyncMock(return_value=SimpleNamespace(metadata=coordinator)),
    )
    monkeypatch.setattr("openswe.threads.summary.is_admin", lambda *args, **kwargs: False)

    review = await WorkflowReview.resolve("worker", metadata, "viewer")

    assert review.records["fp"]["status"] == "pending"
    assert review.records["fp"].get("coordinator_status") == (
        expected if expected != "pending" else None
    )
    assert review.pending is True
    assert metadata == original


async def test_finished_thread_summary_still_reports_outstanding_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.threads.summary import _thread_summary

    monkeypatch.setattr(TaskMembership, "context_for_thread", AsyncMock(return_value=None))
    summary = await _thread_summary(
        {
            "thread_id": "worker",
            "metadata": {"workflow_push_approvals": {"fp": {"status": "pending"}}},
        },
        latest_run_status="success",
        bridge_status=False,
    )
    assert summary["status"] == "finished"
    assert summary["pendingWorkflowApproval"] is True
