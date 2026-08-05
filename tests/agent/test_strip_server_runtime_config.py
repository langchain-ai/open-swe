"""Regression tests for stripping langgraph-api server runtime from factory config."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langgraph.graph.state import RunnableConfig

from agent.scheduler import get_scheduler
from agent.utils.tracing import strip_server_runtime_config, traced_graph_factory


def test_strip_server_runtime_config_removes_private_key() -> None:
    config: RunnableConfig = {
        "configurable": {
            "__pregel_runtime": object(),
            "thread_id": "t1",
        },
        "tags": ["keep"],
    }

    stripped = strip_server_runtime_config(config)

    assert stripped is not config
    assert stripped["tags"] == ["keep"]
    assert stripped["configurable"] == {"thread_id": "t1"}
    assert "__pregel_runtime" in config["configurable"]


def test_strip_server_runtime_config_noop_without_key() -> None:
    config: RunnableConfig = {"configurable": {"thread_id": "t1"}}

    assert strip_server_runtime_config(config) is config


def test_strip_server_runtime_config_noop_without_configurable() -> None:
    config: RunnableConfig = {"tags": ["a"]}

    assert strip_server_runtime_config(config) is config


@pytest.mark.asyncio
async def test_traced_graph_factory_strips_runtime_before_factory() -> None:
    received: dict[str, Any] = {}
    graph = MagicMock(name="graph")

    async def factory(config: RunnableConfig) -> MagicMock:
        received["config"] = config
        return graph

    entrypoint = traced_graph_factory(factory, "test-project")
    runtime_token = object()
    inbound: RunnableConfig = {
        "configurable": {
            "__pregel_runtime": runtime_token,
            "thread_id": "t1",
        }
    }

    async with entrypoint(inbound) as yielded:
        assert yielded is graph

    assert received["config"]["configurable"] == {"thread_id": "t1"}
    assert inbound["configurable"]["__pregel_runtime"] is runtime_token


def test_get_scheduler_does_not_bake_server_runtime() -> None:
    runtime_token = object()
    config: RunnableConfig = {
        "configurable": {
            "__pregel_runtime": runtime_token,
            "schedule_id": "sched-1",
        }
    }

    graph = get_scheduler(config)
    baked = graph.config.get("configurable") if graph.config else None

    assert isinstance(baked, dict)
    assert "__pregel_runtime" not in baked
    assert baked.get("schedule_id") == "sched-1"
