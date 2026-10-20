"""Claim outages must not admit duplicate work or block unrelated deliveries."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.slack import events


@pytest.fixture(autouse=True)
def reset_claims() -> None:
    events.reset_slack_event_claims()


async def test_storage_failure_requests_retry_without_poisoning_local_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.threads.create.side_effect = ConnectionError("storage down")
    client.threads.get.side_effect = ConnectionError("storage down")
    monkeypatch.setattr(events, "get_client", lambda **kwargs: client)
    with pytest.raises(HTTPException) as error:
        await events.claim_slack_event("event")
    assert error.value.status_code == 503
    assert not await events.slack_event_already_seen("event")
    client.threads.create.side_effect = None
    assert await events.claim_slack_event("event") is True
    assert await events.claim_slack_event("event") is False


async def test_claim_recovers_an_acknowledgement_lost_after_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    persisted: dict[str, object] = {}

    async def create(**kwargs: object) -> None:
        persisted["metadata"] = kwargs["metadata"]
        raise ConnectionError("response lost after commit")

    client.threads.create.side_effect = create
    client.threads.get.return_value = persisted
    monkeypatch.setattr(events, "get_client", lambda **kwargs: client)
    assert await events.claim_slack_event("event") is True


async def test_expired_local_claim_rechecks_shared_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr(events.time, "monotonic", lambda: now)
    remote = AsyncMock(return_value=True)
    monkeypatch.setattr(events, "_claim_remotely", remote)
    assert await events.claim_slack_event("event") is True
    assert await events.claim_slack_event("event") is False
    now += 601
    assert not await events.slack_event_already_seen("event")
    assert await events.claim_slack_event("event") is True
    assert remote.await_count == 2


async def test_slow_claim_does_not_block_an_unrelated_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def remote(key: str) -> bool:
        if key == "slow":
            entered.set()
            await release.wait()
        return True

    monkeypatch.setattr(events, "_claim_remotely", remote)
    slow = asyncio.create_task(events.claim_slack_event("slow"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert await asyncio.wait_for(events.claim_slack_event("fast"), timeout=1) is True
    finally:
        release.set()
        await slow
