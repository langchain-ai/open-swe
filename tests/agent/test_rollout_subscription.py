"""A merged agent thread subscribes to later release deploys of its commit."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from openswe.github.http import GitHubAppUnavailable
from openswe.rollout_events import ask_to_monitor_rollout, subscribe_merged_thread
from openswe.run_config import RunConfig
from openswe.tools.request_rollout_check import request_rollout_check

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
@pytest.mark.parametrize(
    ("source", "destination", "reports_to_slack"),
    [
        ("slack", {"channel_id": "C1", "thread_ts": "1.2"}, True),
        ("dashboard", {"channel_id": "C1", "thread_ts": "1.2"}, False),
        ("slack", {"channel_id": "C1"}, False),
        ("slack", None, False),
    ],
)
async def test_rollout_request_destination_survives_web_follow_up(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    destination: dict[str, str] | None,
    reports_to_slack: bool,
) -> None:
    created = _ready(monkeypatch)
    metadata: dict[str, object] = {"source": source}

    async def update(thread_id: str, flag: dict[str, object]) -> SimpleNamespace:
        metadata.update(flag)
        return SimpleNamespace(thread_id=thread_id, runtime="mda", metadata=metadata)

    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig.parse(
                {
                    "thread_id": "thread-1",
                    "source": source,
                    "github_login": "octo",
                    "slack_thread": destination,
                }
            ),
        ),
        patch("openswe.tools.request_rollout_check.index.get", AsyncMock(return_value=True)),
        patch("openswe.tools.request_rollout_check.index.update", update),
        patch("openswe.tools.request_rollout_check.RUNTIMES.copies", return_value=[]),
    ):
        await request_rollout_check()
    metadata["source"] = "dashboard"
    latest_destination = {"channel_id": "C2", "thread_ts": "3.4"}
    metadata["source_context"] = {"slack_thread": latest_destination}
    await subscribe_merged_thread(
        "thread-1", owner="lc", repo="repo", number=7, sha=_SHA, metadata=metadata
    )
    subscription = created[0]
    assert subscription.run_config["slack_thread"] == (
        destination if reports_to_slack else latest_destination
    )


def _skill_file(
    monkeypatch: pytest.MonkeyPatch, body: bytes | None, *, fail: BaseException | None = None
) -> AsyncMock:
    raw_file = AsyncMock(return_value=body)

    @asynccontextmanager
    async def as_app(owner: str, repo: str):
        if fail is not None:
            raise fail
        assert (owner, repo) == ("lc", "repo")
        yield SimpleNamespace(repo=lambda _owner, _name: SimpleNamespace(raw_file=raw_file))

    monkeypatch.setattr("openswe.rollout_events.GitHubClient.as_app", as_app)
    return raw_file


@pytest.mark.asyncio
async def test_ask_to_monitor_rollout_asks_when_a_yes_can_subscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready(monkeypatch)
    raw_file = _skill_file(monkeypatch, b"name: rollout-check\n")
    dispatch = AsyncMock()
    monkeypatch.setattr("openswe.rollout_events.dispatch_agent_run", dispatch)
    await ask_to_monitor_rollout(
        "thread-1", owner="lc", repo="repo", number=7, metadata={}, sha=_SHA.upper()
    )
    raw_file.assert_awaited_once_with(".agents/skills/rollout-check/SKILL.md", _SHA)
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocker",
    ["missing", "unreadable", "unknown_sha"],
)
async def test_ask_to_monitor_rollout_does_not_ask_without_a_rollout_skill(
    monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    _ready(monkeypatch)
    raw_file = _skill_file(
        monkeypatch,
        None if blocker == "missing" else b"name: rollout-check\n",
        fail=GitHubAppUnavailable("no token") if blocker == "unreadable" else None,
    )
    dispatch = AsyncMock()
    monkeypatch.setattr("openswe.rollout_events.dispatch_agent_run", dispatch)
    await ask_to_monitor_rollout(
        "thread-1",
        owner="lc",
        repo="repo",
        number=7,
        metadata={},
        sha="" if blocker == "unknown_sha" else _SHA,
    )
    dispatch.assert_not_awaited()
    if blocker == "unknown_sha":
        raw_file.assert_not_awaited()


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
