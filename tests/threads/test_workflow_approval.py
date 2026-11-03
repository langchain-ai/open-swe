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
