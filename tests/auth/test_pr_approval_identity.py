import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest
from fastapi import HTTPException

from openswe.dashboard import profiles
from openswe.run_config import RunConfig
from openswe.threads import plan_api, workflow_approval, workflow_approval_api


async def test_approval_run_uses_authenticated_actor(monkeypatch):
    metadata = {
        "visibility": "public",
        "owner_type": "user",
        "owner_login": "alice",
        "github_login": "alice",
        "triggering_user_email": "alice@example.com",
        "source": "dashboard",
    }
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}))
    )
    monkeypatch.setattr(langgraph_sdk, "get_client", lambda: client)
    monkeypatch.setattr(plan_api, "fetch_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(
        workflow_approval_api, "fetch_thread_metadata", AsyncMock(return_value=metadata)
    )
    monkeypatch.setattr(
        workflow_approval_api, "decide_workflow_push_approval", AsyncMock(return_value={})
    )
    dispatch = AsyncMock(return_value={"run_id": "approval-run"})
    monkeypatch.setattr(workflow_approval_api, "dispatch_agent_run", dispatch)
    session = {"sub": "Bob", "email": "bob@example.com"}
    await workflow_approval_api.approve_workflow_push("thread-1", "fp", session=session)

    assert dispatch.await_args is not None
    config = {"configurable": dispatch.await_args.args[2]}
    monkeypatch.setattr("openswe.run_config.get_config", lambda: config)
    monkeypatch.setattr(
        profiles,
        "get_valid_access_token",
        AsyncMock(side_effect={"alice": "alice-token", "Bob": "bob-token"}.get),
    )
    opr = importlib.import_module("openswe.tools.open_pull_request")
    assert await opr._resolve_pr_author_token() == ("bob-token", "user")
    cfg = RunConfig.from_runtime()
    assert cfg.user_email == "bob@example.com"
    assert metadata["owner_login"] == "alice"


@pytest.mark.parametrize("approve_first", [True, False])
async def test_workflow_approval_replay_preserves_first_decision(
    monkeypatch: pytest.MonkeyPatch, approve_first: bool
) -> None:
    metadata = {"visibility": "public", "owner_login": "alice", "source": "dashboard"}
    approvals: dict[str, dict[str, object]] = {"fp": {"fingerprint": "fp", "status": "pending"}}

    async def save(_thread_id: str, records: dict[str, dict[str, object]]) -> None:
        approvals.update(records)

    monkeypatch.setattr(workflow_approval.postgres, "configured", lambda: False)
    monkeypatch.setattr(
        workflow_approval,
        "get_workflow_push_approvals",
        AsyncMock(side_effect=lambda _: {key: dict(value) for key, value in approvals.items()}),
    )
    monkeypatch.setattr(workflow_approval, "_save_approvals", save)
    monkeypatch.setattr(
        workflow_approval_api, "fetch_thread_metadata", AsyncMock(return_value=metadata)
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(workflow_approval_api, "dispatch_followup", dispatch)
    first = (
        workflow_approval_api.approve_workflow_push
        if approve_first
        else workflow_approval_api.reject_workflow_push
    )
    second = (
        workflow_approval_api.reject_workflow_push
        if approve_first
        else workflow_approval_api.approve_workflow_push
    )

    await first("thread-1", "fp", session={"sub": "alice"})
    with pytest.raises(HTTPException) as raised:
        await second("thread-1", "fp", session={"sub": "alice"})

    assert raised.value.status_code == 409
    assert approvals["fp"]["status"] == ("approved" if approve_first else "rejected")
    assert dispatch.await_count == int(approve_first)
