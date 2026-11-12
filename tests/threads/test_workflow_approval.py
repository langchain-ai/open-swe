import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from openswe.threads import workflow_approval


@pytest.mark.parametrize("second_approved", [True, False])
async def test_concurrent_decisions_have_one_winner(
    registry_db: None, monkeypatch: pytest.MonkeyPatch, second_approved: bool
) -> None:
    stored = {"fp": {"status": "pending", "fingerprint": "fp"}}

    async def read(thread_id: str) -> dict[str, dict[str, str]]:
        snapshot = deepcopy(stored)
        await asyncio.sleep(0.01)
        return snapshot

    async def save(thread_id: str, approvals: dict[str, dict[str, str]]) -> None:
        stored.update(deepcopy(approvals))

    monkeypatch.setattr(workflow_approval, "get_workflow_push_approvals", read)
    monkeypatch.setattr(workflow_approval, "_save_approvals", AsyncMock(side_effect=save))
    results = await asyncio.gather(
        workflow_approval.decide_workflow_push_approval(
            "thread", "fp", approved=True, actor="alice"
        ),
        workflow_approval.decide_workflow_push_approval(
            "thread", "fp", approved=second_approved, actor="bob"
        ),
    )

    assert sorted(result[1] for result in results if result is not None) == [False, True]
    assert results[0][0] == results[1][0] == stored["fp"]


async def test_repeated_pending_request_becomes_latest(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = {"fp": {"status": "pending", "requested_at": "2026-11-08T14:00:00Z", "notified": True}}
    monkeypatch.setattr(
        workflow_approval, "get_workflow_push_approvals", AsyncMock(return_value=deepcopy(stored))
    )
    monkeypatch.setattr(workflow_approval, "_save_approvals", AsyncMock())
    monkeypatch.setattr(workflow_approval, "now_iso", lambda: "2026-11-08T16:00:00Z")
    record, created = await workflow_approval.ensure_workflow_push_pending(
        "thread",
        fingerprint="fp",
        repo="repo",
        branch="branch",
        base_sha="base",
        head_sha="head",
        files=[],
    )
    assert record["requested_at"] == "2026-11-08T16:00:00Z"
    assert record["first_requested_at"] == "2026-11-08T14:00:00Z"
    assert record["status"] == "pending"
    assert created is False
