"""Tests that get_reviewer_agent and get_chat_agent do not mutate the caller's config."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig


def _make_config(recursion_limit: int = 25) -> RunnableConfig:
    return {
        "configurable": {"thread_id": None},
        "recursion_limit": recursion_limit,
    }


@pytest.mark.asyncio
async def test_get_reviewer_agent_does_not_mutate_caller_config() -> None:
    """get_reviewer_agent must not overwrite the caller's recursion_limit."""
    from agent import reviewer

    config = _make_config(recursion_limit=25)
    original_limit = config["recursion_limit"]

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("agent.reviewer.create_deep_agent", return_value=fake_pregel):
        await reviewer.get_reviewer_agent(config)

    assert config["recursion_limit"] == original_limit, (
        f"get_reviewer_agent mutated caller's recursion_limit: "
        f"expected {original_limit}, got {config['recursion_limit']}"
    )


@pytest.mark.asyncio
async def test_get_reviewer_agent_applies_default_when_limit_unset() -> None:
    """get_reviewer_agent should apply DEFAULT_RECURSION_LIMIT when the caller didn't set one."""
    from agent import reviewer

    config: RunnableConfig = {"configurable": {"thread_id": None}}

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("agent.reviewer.create_deep_agent", return_value=fake_pregel):
        await reviewer.get_reviewer_agent(config)

    # The original config must still be unchanged (no recursion_limit key added)
    assert "recursion_limit" not in config


@pytest.mark.asyncio
async def test_get_chat_agent_does_not_mutate_caller_config() -> None:
    """get_chat_agent must not overwrite the caller's recursion_limit."""
    from agent import chat

    config = _make_config(recursion_limit=50)
    original_limit = config["recursion_limit"]

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("agent.chat.create_deep_agent", return_value=fake_pregel):
        await chat.get_chat_agent(config)

    assert config["recursion_limit"] == original_limit, (
        f"get_chat_agent mutated caller's recursion_limit: "
        f"expected {original_limit}, got {config['recursion_limit']}"
    )


@pytest.mark.asyncio
async def test_get_chat_agent_applies_default_when_limit_unset() -> None:
    """get_chat_agent should apply DEFAULT_RECURSION_LIMIT when the caller didn't set one."""
    from agent import chat

    config: RunnableConfig = {"configurable": {"thread_id": None}}

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("agent.chat.create_deep_agent", return_value=fake_pregel):
        await chat.get_chat_agent(config)

    assert "recursion_limit" not in config


@pytest.mark.asyncio
async def test_get_reviewer_agent_configurable_isolation() -> None:
    """get_reviewer_agent must not mutate the caller's configurable sub-dict."""
    from agent import reviewer

    config: RunnableConfig = {
        "configurable": {"thread_id": None, "custom_key": "sentinel"},
    }
    original_configurable_id = id(config["configurable"])

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("agent.reviewer.create_deep_agent", return_value=fake_pregel):
        await reviewer.get_reviewer_agent(config)

    assert id(config["configurable"]) == original_configurable_id, (
        "get_reviewer_agent replaced the caller's configurable dict"
    )
    assert config["configurable"]["custom_key"] == "sentinel", (
        "get_reviewer_agent mutated the caller's configurable dict"
    )
