"""A merged agent thread subscribes to later release deploys of its commit."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from openswe.prompts import prompt
from openswe.rollout_events import subscribe_merged_thread
from openswe.run_config import RunConfig
from openswe.tools.request_rollout_check import request_rollout_check
from openswe.webhooks.event_subscriptions import EventSummary

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
    client = AsyncMock()

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        saved.update(metadata)

    saved = metadata
    client.threads.update.side_effect = update
    with (
        patch("openswe.tools.request_rollout_check.langgraph_client", return_value=client),
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
    ):
        await request_rollout_check()
    metadata["source"] = "dashboard"
    await subscribe_merged_thread(
        "thread-1", owner="lc", repo="repo", number=7, sha=_SHA, metadata=metadata
    )
    subscription = created[0]
    event_turn = prompt(
        "runs/event-subscription",
        summary=EventSummary(source="deployment", event_type="deployed"),
        details="Target: production",
        instructions=subscription.instructions,
        subscription_id="subscription-1",
        one_shot=False,
    )
    assert ("slack_reply" in event_turn) is reports_to_slack
    if reports_to_slack:
        assert "C1" in event_turn and "1.2" in event_turn and "dashboard" in event_turn
        assert subscription.run_config["slack_thread"] == destination
    else:
        assert "slack_thread" not in subscription.run_config
