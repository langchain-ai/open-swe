from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import reviewer


def test_reviewer_system_prompt_formats_without_keyerror() -> None:
    prompt = reviewer._reviewer_system_prompt(
        "/workspace/repo",
        repo_owner="acme",
        repo_name="repo",
        pr_number=42,
    )
    assert "acme/repo" in prompt
    assert "Common defect patterns" in prompt
    assert "benchmark" not in prompt.lower()
    assert "golden" not in prompt.lower()
    assert "at least 1 finding" not in prompt.lower()


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
async def test_reviewer_applies_eval_model_and_effort_overrides() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "reviewer-thread-id",
            "repo": {"owner": "acme", "name": "repo"},
            "pr_number": 1,
            "pr_url": "https://github.com/acme/repo/pull/1",
            "base_sha": "base",
            "head_sha": "head",
            "reviewer_model_id": "anthropic:claude-opus-4-7",
            "reviewer_reasoning_effort": "high",
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
        patch("agent.reviewer.make_model", return_value=MagicMock()) as make_model,
        patch("agent.reviewer.create_deep_agent", return_value=dummy_agent),
    ):
        await reviewer.get_reviewer_agent(config)

    assert make_model.call_args.args == ("anthropic:claude-opus-4-7",)
    assert make_model.call_args.kwargs["thinking"] == {"type": "adaptive"}
    assert make_model.call_args.kwargs["effort"] == "high"
