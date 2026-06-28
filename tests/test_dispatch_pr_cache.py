"""Tests for the in-process PR context cache in ``agent.dispatch``."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import dispatch


@pytest.fixture(autouse=True)
def _clear_pr_context_cache() -> None:
    dispatch._PR_CONTEXT_CACHE.clear()
    yield
    dispatch._PR_CONTEXT_CACHE.clear()


def _client_stub() -> MagicMock:
    client = MagicMock()
    client.runs = MagicMock()
    client.runs.create = AsyncMock(return_value={"run_id": "run-xyz"})
    return client


def _configurable(pr_number: int = 28399) -> dict[str, Any]:
    return {
        "source": "github",
        "repo": {"owner": "langchain-ai", "name": "langchain"},
        "pr_number": pr_number,
        "branch": "open-swe/fix-28399",
    }


async def test_first_dispatch_records_cache_without_injecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_stub()
    await dispatch.dispatch_agent_run(
        "thread-a", "hello", _configurable(), source="test", client=client
    )
    call = client.runs.create.call_args
    sent_configurable = call.kwargs["config"]["configurable"]
    assert "_cached_pr_context" not in sent_configurable
    assert ("langchain-ai", "langchain", 28399) in dispatch._PR_CONTEXT_CACHE


async def test_second_dispatch_on_same_pr_injects_cached_context() -> None:
    client = _client_stub()
    await dispatch.dispatch_agent_run(
        "thread-a", "first", _configurable(), source="test", client=client
    )
    await dispatch.dispatch_agent_run(
        "thread-b", "second", _configurable(), source="test", client=client
    )
    sent_configurable = client.runs.create.call_args.kwargs["config"]["configurable"]
    cached = sent_configurable["_cached_pr_context"]
    assert cached["pr_number"] == 28399
    assert cached["repo"] == {"owner": "langchain-ai", "name": "langchain"}
    assert cached["thread_id"] == "thread-a"
    assert cached["age_seconds"] >= 0


async def test_same_thread_redispatch_does_not_inject_cache() -> None:
    client = _client_stub()
    await dispatch.dispatch_agent_run(
        "thread-a", "first", _configurable(), source="test", client=client
    )
    await dispatch.dispatch_agent_run(
        "thread-a", "second", _configurable(), source="test", client=client
    )
    sent_configurable = client.runs.create.call_args.kwargs["config"]["configurable"]
    assert "_cached_pr_context" not in sent_configurable


async def test_stale_cache_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_stub()
    times = iter([1000.0, 1000.0 + dispatch._PR_CONTEXT_TTL_SECONDS + 1])
    monkeypatch.setattr(dispatch.time, "time", lambda: next(times))
    await dispatch.dispatch_agent_run(
        "thread-a", "first", _configurable(), source="test", client=client
    )
    await dispatch.dispatch_agent_run(
        "thread-b", "second", _configurable(), source="test", client=client
    )
    sent_configurable = client.runs.create.call_args.kwargs["config"]["configurable"]
    assert "_cached_pr_context" not in sent_configurable


async def test_dispatch_without_pr_number_is_a_noop() -> None:
    client = _client_stub()
    await dispatch.dispatch_agent_run(
        "thread-a", "hi", {"source": "slack"}, source="test", client=client
    )
    assert dispatch._PR_CONTEXT_CACHE == {}


async def test_dispatch_with_different_pr_does_not_share_cache() -> None:
    client = _client_stub()
    await dispatch.dispatch_agent_run(
        "thread-a", "first", _configurable(28399), source="test", client=client
    )
    await dispatch.dispatch_agent_run(
        "thread-b", "second", _configurable(99999), source="test", client=client
    )
    sent_configurable = client.runs.create.call_args.kwargs["config"]["configurable"]
    assert "_cached_pr_context" not in sent_configurable
