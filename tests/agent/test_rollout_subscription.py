"""A merged agent thread subscribes to later release deploys of its commit."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openswe.rollout_events import ask_to_monitor_rollout, subscribe_merged_thread

_SHA = "ab" * 20


def _ready(
    monkeypatch: pytest.MonkeyPatch, *, existing: list[object] | None = None
) -> list[object]:
    created: list[object] = []

    async def create(self: object) -> object:
        created.append(self)
        return self

    monkeypatch.setattr("openswe.rollout_events.configured", lambda: True)
    monkeypatch.setattr(
        "openswe.rollout_events.Repository.get",
        AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    )
    monkeypatch.setattr(
        "openswe.rollout_events.workspace_for_repo", AsyncMock(return_value="default")
    )
    monkeypatch.setattr(
        "openswe.rollout_events.WORKSPACES.id_for_slug", AsyncMock(return_value=uuid4())
    )
    monkeypatch.setattr(
        "openswe.rollout_events.EventSubscription.for_thread",
        AsyncMock(return_value=existing or []),
    )
    monkeypatch.setattr("openswe.rollout_events.EventSubscription.create", create)
    return created


@pytest.mark.asyncio
async def test_subscribe_merged_thread_listens_for_the_merge_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _ready(monkeypatch)
    await subscribe_merged_thread(
        "thread-1",
        owner="lc",
        repo="repo",
        number=7,
        sha=_SHA.upper(),
        metadata={
            "github_login": "octo",
            "triggering_user_email": "octo@example.com",
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}},
        },
    )
    assert len(created) == 1
    subscription = created[0]
    assert subscription.payload_match == {"commits": [_SHA]}
    assert subscription.sources == ["deployment"]
    assert subscription.event_types == ["deployed"]
    assert subscription.one_shot is False
    assert subscription.pull_request_id is None
    assert subscription.multitask_strategy == "enqueue"
    assert subscription.run_config["repo"] == {"owner": "lc", "name": "repo"}
    assert subscription.run_config["pr_number"] == 7
    assert subscription.run_config["github_login"] == "octo"
    assert subscription.run_config["user_email"] == "octo@example.com"
    assert subscription.run_config["slack_thread"] == {"channel_id": "C1", "thread_ts": "1.2"}
    assert "every target" in subscription.instructions


@pytest.mark.asyncio
async def test_subscribe_merged_thread_does_not_listen_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(
        sources=["deployment"],
        event_types=["deployed"],
        payload_match={"commits": [_SHA]},
    )
    created = _ready(monkeypatch, existing=[existing])
    await subscribe_merged_thread(
        "thread-1", owner="lc", repo="repo", number=7, sha=_SHA, metadata={}
    )
    assert created == []


@pytest.mark.asyncio
async def test_ask_to_monitor_rollout_asks_when_a_yes_can_subscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready(monkeypatch)
    dispatch = AsyncMock()
    monkeypatch.setattr("openswe.rollout_events.dispatch_agent_run", dispatch)
    await ask_to_monitor_rollout(
        "thread-1", owner="lc", repo="repo", number=7, metadata={}, sha=_SHA.upper()
    )
    dispatch.assert_awaited_once()
    message = dispatch.await_args.args[1]
    assert isinstance(message, str)
    assert ".agents/skills/rollout-check/SKILL.md" in message
    assert _SHA in message
    assert "stop without messaging" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["unconfigured", "no_workspace"])
async def test_ask_to_monitor_rollout_does_not_ask_when_a_yes_cannot_subscribe(
    monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    monkeypatch.setattr("openswe.rollout_events.configured", lambda: blocker != "unconfigured")
    monkeypatch.setattr("openswe.rollout_events.Repository.get", AsyncMock(return_value=None))
    monkeypatch.setattr("openswe.rollout_events.workspace_for_repo", AsyncMock(return_value=""))
    dispatch = AsyncMock()
    monkeypatch.setattr("openswe.rollout_events.dispatch_agent_run", dispatch)
    await ask_to_monitor_rollout("thread-1", owner="lc", repo="repo", number=7, metadata={})
    dispatch.assert_not_awaited()
