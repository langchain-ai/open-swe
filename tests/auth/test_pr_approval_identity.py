import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest

from agent.dashboard import plan_api, profiles, workflow_approval_api
from agent.run_config import RunConfig
from agent.slack import webhook as slack_webhook
from agent.slack.request import SlackRequest


@pytest.mark.parametrize(
    "action", ["approve", "revise", "workflow", "slack_approve", "slack_unlinked"]
)
async def test_approval_run_uses_authenticated_actor(monkeypatch, fake_store, action):
    metadata = {
        "visibility": "public",
        "owner_type": "user",
        "owner_login": "alice",
        "github_login": "alice",
        "triggering_user_email": "alice@example.com",
        "source": "dashboard",
        "plan_mode": True,
        "plan_status": "ready",
    }
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": metadata}))
    )
    monkeypatch.setattr(langgraph_sdk, "get_client", lambda: client)
    monkeypatch.setattr(plan_api, "fetch_thread_metadata", AsyncMock(return_value=metadata))
    monkeypatch.setattr(plan_api, "get_plan_content", AsyncMock(return_value={"status": "ready"}))
    monkeypatch.setattr(plan_api, "set_plan_status", AsyncMock())
    monkeypatch.setattr(plan_api, "list_plan_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(plan_api, "_maybe_post_plan_approved_to_slack", AsyncMock())
    monkeypatch.setattr(
        workflow_approval_api, "fetch_thread_metadata", AsyncMock(return_value=metadata)
    )
    monkeypatch.setattr(
        workflow_approval_api, "decide_workflow_push_approval", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        slack_webhook.common,
        "login_for_slack_id",
        AsyncMock(return_value=None if action == "slack_unlinked" else "Bob"),
    )
    monkeypatch.setattr(slack_webhook.common, "get_slack_user_info", AsyncMock(return_value={}))
    dispatch = AsyncMock(return_value={"run_id": "approval-run"})
    monkeypatch.setattr(plan_api, "dispatch_agent_run", dispatch)
    session = {"sub": "Bob", "email": "bob@example.com"}
    if action == "approve":
        await plan_api.approve_plan("thread-1", session=session)
    elif action == "revise":
        await plan_api.reject_plan("thread-1", session=session)
    elif action == "workflow":
        await workflow_approval_api.approve_workflow_push("thread-1", "fp", session=session)
    else:
        await slack_webhook.process_slack_plan_approval(
            SlackRequest(channel_id="C1", thread_ts="1.0", thread_id="thread-1", user_id="U2"),
            None,
        )

    assert dispatch.await_args is not None
    config = {"configurable": dispatch.await_args.args[2]}
    monkeypatch.setattr("agent.run_config.get_config", lambda: config)
    monkeypatch.setattr(
        profiles,
        "get_valid_access_token",
        AsyncMock(side_effect={"alice": "alice-token", "Bob": "bob-token"}.get),
    )
    opr = importlib.import_module("agent.tools.open_pull_request")
    if action == "slack_unlinked":
        with pytest.raises(RuntimeError, match="requester"):
            await opr._resolve_pr_author_token()
    else:
        assert await opr._resolve_pr_author_token() == ("bob-token", "user")
    cfg = RunConfig.from_runtime()
    assert cfg.user_email == (None if action.startswith("slack_") else "bob@example.com")
    assert metadata["owner_login"] == "alice"
