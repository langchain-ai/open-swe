from unittest.mock import AsyncMock, MagicMock

import pytest
from langsmith.sandbox import SandboxRetryableConnectionError

import agent.server as server


def _middleware() -> server.PrepareAgentRunMiddleware:
    return server.PrepareAgentRunMiddleware(
        thread_id="thread-1",
        config={"configurable": {"source": "dashboard"}},
        profile_login=None,
        repo_instructions=None,
        model_id="openai:gpt-5",
        effort=None,
        title_model=MagicMock(),
        source="dashboard",
        user_email="",
        linear_project_id="",
        linear_issue_number="",
        draft_prs=False,
        recent_thread_context_enabled=False,
        admin_workspaces=False,
    )


@pytest.mark.asyncio
async def test_prepare_notifies_without_sandbox_id_for_retryable_attach_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = SandboxRetryableConnectionError("gateway unavailable")
    proxy = MagicMock()
    proxy.ready = AsyncMock(side_effect=error)
    notify = AsyncMock()
    monkeypatch.setattr(server, "get_or_create_sandbox_backend_proxy", lambda _: proxy)
    monkeypatch.setattr(server, "schedule_thread_title_generation", MagicMock())
    monkeypatch.setattr(server, "resolve_github_token", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(server, "_resolve_prompt_default_repo", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "resolve_triggering_user_identity", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "post_sandbox_unreachable_notification", notify)
    monkeypatch.setattr(server, "SANDBOX_ATTACH_MAX_ELAPSED", 0)

    with pytest.raises(SandboxRetryableConnectionError):
        await _middleware()._prepare({"messages": []}, MagicMock())

    notify.assert_awaited_once_with(
        {"configurable": {"source": "dashboard", "draft_prs": False}}, sandbox_id=None
    )
