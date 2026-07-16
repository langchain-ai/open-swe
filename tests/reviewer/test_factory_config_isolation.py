"""Tests that get_reviewer_agent and get_chat_agent do not mutate the caller's config."""

from __future__ import annotations

from typing import cast
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
    original_limit = config.get("recursion_limit")
    assert original_limit is not None

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("agent.reviewer.create_deep_agent", return_value=fake_pregel):
        await reviewer.get_reviewer_agent(config)

    assert config.get("recursion_limit") == original_limit, (
        f"get_reviewer_agent mutated caller's recursion_limit: "
        f"expected {original_limit}, got {config.get('recursion_limit')}"
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

    assert "recursion_limit" not in config


@pytest.mark.asyncio
async def test_get_chat_agent_does_not_mutate_caller_config() -> None:
    """get_chat_agent must not overwrite the caller's recursion_limit."""
    from agent import chat

    config = _make_config(recursion_limit=50)
    original_limit = config.get("recursion_limit")
    assert original_limit is not None

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch("agent.chat.create_deep_agent", return_value=fake_pregel):
        await chat.get_chat_agent(config)

    assert config.get("recursion_limit") == original_limit, (
        f"get_chat_agent mutated caller's recursion_limit: "
        f"expected {original_limit}, got {config.get('recursion_limit')}"
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


@pytest.mark.parametrize(
    ("module_name", "factory_name"),
    [("agent.reviewer", "get_reviewer_agent"), ("agent.chat", "get_chat_agent")],
)
@pytest.mark.asyncio
async def test_factory_copies_config_dicts_but_preserves_runtime_objects(
    module_name: str, factory_name: str
) -> None:
    """Factory config isolation must preserve callback and configurable value identities."""
    module = __import__(module_name, fromlist=[factory_name])
    factory = getattr(module, factory_name)
    callback = object()
    configurable_value = object()
    callbacks = [callback]
    config = cast(
        RunnableConfig,
        {
            "configurable": {"thread_id": None, "custom_key": configurable_value},
            "callbacks": callbacks,
        },
    )

    fake_pregel = MagicMock()
    fake_pregel.with_config = MagicMock(return_value=fake_pregel)

    with patch(f"{module_name}.create_deep_agent", return_value=fake_pregel):
        await factory(config)

    assert fake_pregel.with_config.call_args is not None
    bound_config = cast(dict[str, object], fake_pregel.with_config.call_args.args[0])
    configurable = cast(dict[str, object], bound_config["configurable"])
    original_configurable = config.get("configurable")
    assert isinstance(original_configurable, dict)
    assert bound_config is not config
    assert configurable is not original_configurable
    assert configurable["custom_key"] is configurable_value
    assert bound_config["callbacks"] is callbacks
    assert cast(list[object], bound_config["callbacks"])[0] is callback
    assert "recursion_limit" not in config
    assert config.get("configurable") == {"thread_id": None, "custom_key": configurable_value}
