from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import reviewer


class _DummyAgent:
    def with_config(self, config: dict[str, object]) -> _DummyAgent:
        self.config = config
        return self


@pytest.mark.asyncio
async def test_reviewer_uses_cached_thread_token_for_slack_review_request() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "reviewer-thread-id",
            "source": "slack",
            "review_requested": True,
        },
        "metadata": {},
    }
    dummy_agent = _DummyAgent()

    with (
        patch(
            "agent.reviewer.get_github_token_from_thread",
            new_callable=AsyncMock,
            return_value=("app-token", "encrypted-token", None),
        ) as mock_get_thread_token,
        patch("agent.reviewer.resolve_github_token", new_callable=AsyncMock) as mock_resolve_token,
        patch(
            "agent.reviewer.ensure_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch("agent.reviewer.make_model", return_value=MagicMock()),
        patch("agent.reviewer.create_deep_agent", return_value=dummy_agent),
    ):
        await reviewer.get_reviewer_agent(config)

    metadata = config["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["github_token_encrypted"] == "encrypted-token"
    mock_get_thread_token.assert_awaited_once_with("reviewer-thread-id")
    mock_resolve_token.assert_not_called()


@pytest.mark.asyncio
async def test_reviewer_prompt_requires_verification_before_add_finding() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "reviewer-thread-id",
            "repo": {"owner": "acme", "name": "repo"},
            "pr_number": 1,
            "pr_url": "https://github.com/acme/repo/pull/1",
            "base_sha": "base",
            "head_sha": "head",
        },
        "metadata": {},
    }
    dummy_agent = _DummyAgent()

    with (
        patch(
            "agent.reviewer.ensure_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "agent.reviewer.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch("agent.reviewer.make_model", return_value=MagicMock()),
        patch("agent.reviewer.create_deep_agent", return_value=dummy_agent) as create_agent,
    ):
        await reviewer.get_reviewer_agent(config)

    system_prompt = create_agent.call_args.kwargs["system_prompt"]
    assert "Do **not** call `add_finding` while" in system_prompt
    assert "the failure path is supported by concrete code" in system_prompt
    assert "Clone the repo before finalizing any non-trivial finding" in system_prompt
