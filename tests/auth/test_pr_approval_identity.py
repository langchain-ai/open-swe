import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk

from agent.dashboard import profiles
from agent.run_config import RunConfig
from agent.threads import plan_api, workflow_approval_api


async def test_approval_run_uses_authenticated_actor(monkeypatch, fake_store):
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
    monkeypatch.setattr("agent.run_config.get_config", lambda: config)
    monkeypatch.setattr(
        profiles,
        "get_valid_access_token",
        AsyncMock(side_effect={"alice": "alice-token", "Bob": "bob-token"}.get),
    )
    opr = importlib.import_module("agent.tools.open_pull_request")
    assert await opr._resolve_pr_author_token() == ("bob-token", "user")
    cfg = RunConfig.from_runtime()
    assert cfg.user_email == "bob@example.com"
    assert metadata["owner_login"] == "alice"
