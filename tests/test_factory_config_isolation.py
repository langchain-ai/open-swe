"""Regression tests for #1584.

The ``get_reviewer_agent`` / ``get_chat_agent`` factories must treat the caller's
``RunnableConfig`` as read-only: per the langgraph.json entrypoint contract they
derive a compiled graph from a per-run config and must not mutate the dict the
caller passed in. Before the fix they wrote ``recursion_limit`` (and, further
down, the PR diff / GitHub App token) straight onto the caller's dict, silently
overriding a caller's explicit limit and leaking data across runs that share a
base config.

These exercise the offline early-return path (no ``thread_id``), which is enough
to prove the top-of-factory defensive copy: the caller's dict is left untouched.
"""

from __future__ import annotations

import copy
from typing import Any

from agent.chat import get_chat_agent
from agent.reviewer import get_reviewer_agent


def _config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"configurable": {"some_key": "value"}}
    base.update(overrides)
    return base


async def test_get_reviewer_agent_preserves_caller_config() -> None:
    config = _config(recursion_limit=200)
    snapshot = copy.deepcopy(config)

    await get_reviewer_agent(config)

    # An explicit recursion_limit must survive (setdefault, not overwrite), and
    # nothing else may be written back onto the caller's dict.
    assert config == snapshot


async def test_get_chat_agent_preserves_caller_config() -> None:
    config = _config(recursion_limit=200)
    snapshot = copy.deepcopy(config)

    await get_chat_agent(config)

    assert config == snapshot


async def test_get_reviewer_agent_does_not_inject_default_into_caller_config() -> None:
    # When the caller sets no limit, the factory applies its default on its own
    # copy without mutating the caller's dict.
    config = _config()
    snapshot = copy.deepcopy(config)

    await get_reviewer_agent(config)

    assert config == snapshot
    assert "recursion_limit" not in config


async def test_get_chat_agent_does_not_inject_default_into_caller_config() -> None:
    config = _config()
    snapshot = copy.deepcopy(config)

    await get_chat_agent(config)

    assert config == snapshot
    assert "recursion_limit" not in config
