"""A pull request's dashboard status is read from GitHub once per change."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from openswe.github import pull_request_dashboard_routes as routes
from openswe.github.http import GitHubClient
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import OpenPullRequest, PullRequestClient
from openswe.github.shared_status import SharedPullRequestStatus
from openswe.ui_invalidations.topics import Topic


@pytest.fixture
def shared_cache(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The platform's cross-replica cache, as a dict."""
    store: dict[str, object] = {}

    async def get(key: str) -> object:
        return store.get(key)

    async def put(key: str, value: object, ttl: timedelta | None = None) -> None:
        store[key] = value

    monkeypatch.setattr("langgraph_api.cache.cache_get", get)
    monkeypatch.setattr("langgraph_api.cache.cache_set", put)
    return store


def _statuses(store: dict[str, object]) -> dict[str, object]:
    """The shared statuses, without the claims replicas leave while reading."""
    return {key: value for key, value in store.items() if ":claim:" not in key}


async def _another_replica_claims(store: dict[str, object], *, ago: timedelta) -> datetime:
    """Leave a claim as another replica's read, begun ``ago``, would; its start."""
    _, now = await Topic.last_changed(Topic.PULL_REQUESTS.keyed(_KEY))
    started_at = now - ago
    store[_CLAIM] = {"started_at": started_at.isoformat(), "done": False}
    return started_at


_KEY = PullRequestKey.of("lc", "repo", 7)
_CLAIM = f"openswe:pull-request-status:v2:claim:{_KEY}"
_STATUS = f"openswe:pull-request-status:v2:{_KEY}"


def _pull() -> PullRequestClient:
    return GitHubClient(AsyncMock()).repo("lc", "repo").pull_request(7)


def _read(title: str = "Fix") -> OpenPullRequest:
    """A status GitHub answered in full."""
    return OpenPullRequest(
        repo="lc/repo",
        number=7,
        title=title,
        status_available=True,
        ci="passing",
        reviewers=[],
        unresolved_threads=0,
    )


async def test_viewers_share_one_read_until_the_pull_request_changes(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = 0
    answer = asyncio.Event()

    async def load(_: PullRequestClient) -> OpenPullRequest:
        nonlocal loads
        loads += 1
        await answer.wait()
        return _read()

    monkeypatch.setattr(PullRequestClient, "load", load)

    # Every open tab refetches on the same notification.
    tabs = asyncio.gather(*(SharedPullRequestStatus.read(_pull()) for _ in range(3)))
    await asyncio.sleep(0.05)
    answer.set()
    assert [status.title for status in await tabs if status] == ["Fix"] * 3
    assert loads == 1

    assert await SharedPullRequestStatus.read(_pull()) is not None
    assert loads == 1

    await Topic.PULL_REQUEST_CHECKS.invalidate(key=PullRequestKey.of("lc", "repo", 7))
    assert await SharedPullRequestStatus.read(_pull()) is not None
    assert loads == 2


@pytest.mark.parametrize(
    "unread",
    [
        {"status_available": False},
        {"ci": "unknown"},
        {"reviewers": None},
        {"unresolved_threads": None},
    ],
)
async def test_a_read_github_did_not_fully_answer_is_not_shared(
    registry_db: None,
    shared_cache: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    unread: dict[str, object],
) -> None:
    load = AsyncMock(return_value=_read().model_copy(update=unread))
    monkeypatch.setattr(PullRequestClient, "load", load)

    await SharedPullRequestStatus.read(_pull())
    await SharedPullRequestStatus.read(_pull())

    assert load.await_count == 2
    assert _statuses(shared_cache) == {}


async def test_viewers_waiting_on_a_degraded_read_make_their_own(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = asyncio.Event()
    reads = [_read().model_copy(update={"status_available": False}), _read("Own")]

    async def load(_: PullRequestClient) -> OpenPullRequest:
        status = reads.pop(0)
        if not status.status_available:
            # The first viewer's token is rate limited.
            await answer.wait()
        return status

    monkeypatch.setattr(PullRequestClient, "load", load)

    first = asyncio.ensure_future(SharedPullRequestStatus.read(_pull()))
    await asyncio.sleep(0.05)
    second = asyncio.ensure_future(SharedPullRequestStatus.read(_pull()))
    await asyncio.sleep(0.05)
    # CI moves on while the second viewer waits.
    await Topic.PULL_REQUEST_CHECKS.invalidate(key=PullRequestKey.of("lc", "repo", 7))
    answer.set()

    assert (await first) is not None and not (await first).status_available
    assert (await second) == _read("Own")
    # The read that worked is shared from then on.
    assert await SharedPullRequestStatus.read(_pull()) == _read("Own")
    assert reads == []


async def test_a_failed_action_still_refreshes_the_shared_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A merge can resolve conversations and then fail.
    monkeypatch.setattr(
        routes, "act_on_pull_request", AsyncMock(side_effect=HTTPException(409, "not merged"))
    )
    monkeypatch.setattr(routes.GitHubClient, "as_user", lambda _: _OpenClient())
    invalidate = AsyncMock()
    monkeypatch.setattr(Topic.PULL_REQUESTS, "invalidate", invalidate)

    with pytest.raises(HTTPException, match="not merged"):
        await routes.api_act_on_pull_request(
            "lc", "repo", 7, MagicMock(), session={"sub": "octocat"}
        )

    invalidate.assert_awaited_once_with(key=PullRequestKey.of("lc", "repo", 7))


class _OpenClient:
    async def __aenter__(self) -> GitHubClient:
        return GitHubClient(AsyncMock())

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_a_replica_waits_on_another_replicas_read_instead_of_its_own(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read("Mine"))
    monkeypatch.setattr(PullRequestClient, "load", load)
    started_at = await _another_replica_claims(shared_cache, ago=timedelta(seconds=1))

    async def the_other_replica_finishes() -> None:
        await asyncio.sleep(0.3)
        shared_cache[_STATUS] = {
            "read_at": started_at.isoformat(),
            "status": _read("Theirs").model_dump(mode="json"),
        }

    finishing = asyncio.ensure_future(the_other_replica_finishes())
    status = await SharedPullRequestStatus.read(_pull())
    await finishing

    assert status is not None and status.title == "Theirs"
    load.assert_not_awaited()


async def test_a_replica_reads_itself_once_the_one_it_waits_on_shares_nothing(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read("Mine"))
    monkeypatch.setattr(PullRequestClient, "load", load)
    started_at = await _another_replica_claims(shared_cache, ago=timedelta(seconds=1))

    async def the_other_replica_gets_a_partial_read() -> None:
        await asyncio.sleep(0.3)
        shared_cache[_CLAIM] = {"started_at": started_at.isoformat(), "done": True}

    finishing = asyncio.ensure_future(the_other_replica_gets_a_partial_read())
    status = await asyncio.wait_for(SharedPullRequestStatus.read(_pull()), timeout=3)
    await finishing

    assert status is not None and status.title == "Mine"
    load.assert_awaited_once()


async def test_a_claim_from_before_the_last_change_or_long_ago_is_not_waited_on(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read())
    monkeypatch.setattr(PullRequestClient, "load", load)

    await _another_replica_claims(shared_cache, ago=timedelta(seconds=1))
    await Topic.PULL_REQUESTS.invalidate(key=_KEY)
    await asyncio.wait_for(SharedPullRequestStatus.read(_pull()), timeout=0.2)

    await Topic.PULL_REQUESTS.invalidate(key=_KEY)
    await _another_replica_claims(shared_cache, ago=timedelta(minutes=1))
    await asyncio.wait_for(SharedPullRequestStatus.read(_pull()), timeout=0.2)

    assert load.await_count == 2


async def test_a_replicas_own_read_is_claimed_then_released(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    claimed_while_reading: list[object] = []

    async def load(_: PullRequestClient) -> OpenPullRequest:
        claimed_while_reading.append(_claim_done(shared_cache))
        return _read()

    monkeypatch.setattr(PullRequestClient, "load", load)

    await SharedPullRequestStatus.read(_pull())

    assert claimed_while_reading == [False]
    assert _claim_done(shared_cache) is True


def _claim_done(store: dict[str, object]) -> object:
    claim = store[_CLAIM]
    assert isinstance(claim, dict)
    return claim["done"]
