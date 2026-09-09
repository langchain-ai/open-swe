"""Linear delivery freshness and once-only claims."""

from datetime import UTC, datetime, timedelta

import pytest

from agent.linear import deliveries
from agent.webhooks import claims as webhook_claims


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("age", "fresh"),
    [
        (timedelta(0), True),
        (timedelta(seconds=59), True),
        (timedelta(seconds=60), True),
        (timedelta(seconds=61), False),
        (timedelta(seconds=-1), False),
    ],
)
def test_is_fresh_window(age: timedelta, fresh: bool) -> None:
    assert deliveries.is_fresh(_ms(NOW - age), now=NOW) is fresh


def test_is_fresh_honors_a_wider_window() -> None:
    assert deliveries.is_fresh(_ms(NOW - timedelta(seconds=90)), now=NOW, max_age_seconds=120)


class _FakeThreads:
    def __init__(self) -> None:
        self.ids: set[str] = set()

    async def create(self, *, thread_id: str, **_kwargs: object) -> None:
        if thread_id in self.ids:
            raise RuntimeError("conflict")
        self.ids.add(thread_id)

    async def get(self, thread_id: str) -> dict[str, str]:
        if thread_id not in self.ids:
            raise KeyError(thread_id)
        return {"thread_id": thread_id}


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


@pytest.fixture(autouse=True)
def _reset_claims() -> None:
    deliveries.reset_delivery_claims()


async def test_a_delivery_is_claimed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr(webhook_claims, "get_client", lambda url: client)

    assert await deliveries.claim_delivery("delivery-1") is True
    assert await deliveries.claim_delivery("delivery-1") is False
    assert await deliveries.claim_delivery("delivery-2") is True


async def test_a_delivery_claimed_by_another_instance_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    monkeypatch.setattr(webhook_claims, "get_client", lambda url: client)
    client.threads.ids.add(webhook_claims.DeliveryClaims(("linear", "deliveries")).thread_id("d3"))

    assert await deliveries.claim_delivery("d3") is False


async def test_an_unreachable_platform_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenThreads:
        async def create(self, **_kwargs: object) -> None:
            raise RuntimeError("platform down")

        async def get(self, _thread_id: str) -> dict[str, str]:
            raise RuntimeError("platform down")

    class _BrokenClient:
        threads = _BrokenThreads()

    monkeypatch.setattr(webhook_claims, "get_client", lambda url: _BrokenClient())

    assert await deliveries.claim_delivery("delivery-4") is True
    assert await deliveries.claim_delivery("delivery-4") is True
