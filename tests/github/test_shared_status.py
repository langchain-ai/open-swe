"""A pull request's dashboard status is read from GitHub once per change."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from openswe.github import pull_request_dashboard_routes as routes
from openswe.github.http import CommitCI, GitHubClient, RepoClient
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import OpenPullRequest, PullRequestClient
from openswe.github.shared_status import SharedPullRequestStatus
from openswe.review import conversation
from openswe.review import routes as review_routes
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
    _, now = await Topic.last_changed(Topic.PULL_REQUEST_STATE.keyed(_KEY))
    started_at = now - ago
    store[_CLAIM] = {"started_at": started_at.isoformat(), "done": False}
    return started_at


_KEY = PullRequestKey.of("lc", "repo", 7)
_CLAIM = f"openswe:pull-request-status:v3:claim:{_KEY}"
_STATUS = f"openswe:pull-request-status:v3:{_KEY}"


_CI = CommitCI([{"name": "test", "status": "completed", "conclusion": "success"}], [])


def _answers(
    monkeypatch: pytest.MonkeyPatch,
    load: Callable[[PullRequestClient], Awaitable[OpenPullRequest | None]],
) -> None:
    """Have the full read answer ``load``'s status, read beside ``_CI``."""

    async def load_with_ci(pull: PullRequestClient) -> tuple[OpenPullRequest | None, CommitCI]:
        return await load(pull), _CI

    monkeypatch.setattr(PullRequestClient, "load_with_ci", load_with_ci)


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
        head_sha="abc123",
        mergeable=True,
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

    _answers(monkeypatch, load)

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


async def test_robins_own_changes_to_a_pull_request_keep_its_shared_status(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read())
    _answers(monkeypatch, load)
    assert await SharedPullRequestStatus.read(_pull()) is not None

    # Such as its reviewer's findings, which refresh its views but not what GitHub says.
    await Topic.PULL_REQUESTS.invalidate(key=_KEY)
    assert await SharedPullRequestStatus.read(_pull()) is not None
    assert load.await_count == 1

    await Topic.invalidate_pull_request(_KEY, *Topic.for_pull_request_event("pull_request"))
    assert await SharedPullRequestStatus.read(_pull()) is not None
    assert load.await_count == 2


@pytest.mark.parametrize(
    ("event", "topics"),
    [
        ("pull_request", ["pull-requests", "pull-request-state"]),
        ("pull_request_review", ["pull-requests", "pull-request-state"]),
        ("issue_comment", ["pull-requests"]),
        ("check_run", ["pull-request-checks"]),
    ],
)
def test_only_deliveries_that_can_change_a_pull_requests_status_refresh_it(
    event: str, topics: list[str]
) -> None:
    assert [topic.name for topic in Topic.for_pull_request_event(event)] == topics


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
    _answers(monkeypatch, load)

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

    _answers(monkeypatch, load)

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
    monkeypatch.setattr(Topic, "invalidate_pull_request", invalidate)

    with pytest.raises(HTTPException, match="not merged"):
        await routes.api_act_on_pull_request(
            "lc", "repo", 7, MagicMock(), session={"sub": "octocat"}
        )

    invalidate.assert_awaited_once_with(
        PullRequestKey.of("lc", "repo", 7), Topic.PULL_REQUESTS, Topic.PULL_REQUEST_STATE
    )


@pytest.mark.parametrize(
    "write",
    [
        lambda session: review_routes.api_update_pull_request_title(
            "lc", "repo", 7, review_routes.PullRequestTitleUpdate(title="Fix"), session=session
        ),
        lambda session: review_routes.api_post_review_comment(
            "lc", "repo", 7, MagicMock(), session=session
        ),
        lambda session: review_routes.api_submit_pull_request_review(
            "lc", "repo", 7, review_routes.PullRequestReviewSubmit(event="APPROVE"), session=session
        ),
        lambda session: conversation.api_set_review_thread_resolution(
            "lc", "repo", 7, "THREAD", conversation.ThreadResolution(resolved=True), session=session
        ),
    ],
    ids=["title", "review comment", "review", "thread resolution"],
)
async def test_a_dashboard_write_to_github_refreshes_the_shared_status(
    monkeypatch: pytest.MonkeyPatch, write: Callable[[dict[str, str]], Awaitable[object]]
) -> None:
    # A badge read right after approving must not be the status from before it.
    for module in (review_routes, conversation):
        monkeypatch.setattr(module, "require_repo_access_for_user", AsyncMock())
        monkeypatch.setattr(module.GitHubClient, "as_user", lambda _: _OpenClient())
    monkeypatch.setattr(PullRequestClient, "update_title", AsyncMock())
    monkeypatch.setattr(PullRequestClient, "set_thread_resolved", AsyncMock())
    monkeypatch.setattr(review_routes.PostedReviewComment, "post", AsyncMock())
    monkeypatch.setattr(review_routes.SubmittedReview, "submit", AsyncMock())
    invalidate = AsyncMock()
    monkeypatch.setattr(Topic, "invalidate_pull_request", invalidate)

    await write({"sub": "octocat"})

    invalidate.assert_awaited_once_with(
        PullRequestKey.of("lc", "repo", 7), Topic.PULL_REQUESTS, Topic.PULL_REQUEST_STATE
    )


class _OpenClient:
    async def __aenter__(self) -> GitHubClient:
        return GitHubClient(AsyncMock())

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_a_replica_waits_on_another_replicas_read_instead_of_its_own(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read("Mine"))
    _answers(monkeypatch, load)
    started_at = await _another_replica_claims(shared_cache, ago=timedelta(seconds=1))

    async def the_other_replica_finishes() -> None:
        await asyncio.sleep(0.3)
        shared_cache[_STATUS] = {
            "read_at": started_at.isoformat(),
            "checked_at": started_at.isoformat(),
            "ci": None,
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
    _answers(monkeypatch, load)
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
    _answers(monkeypatch, load)

    await _another_replica_claims(shared_cache, ago=timedelta(seconds=1))
    await Topic.PULL_REQUEST_STATE.invalidate(key=_KEY)
    await asyncio.wait_for(SharedPullRequestStatus.read(_pull()), timeout=0.2)

    await Topic.PULL_REQUEST_STATE.invalidate(key=_KEY)
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

    _answers(monkeypatch, load)

    await SharedPullRequestStatus.read(_pull())

    assert claimed_while_reading == [False]
    assert _claim_done(shared_cache) is True


def _claim_done(store: dict[str, object]) -> object:
    claim = store[_CLAIM]
    assert isinstance(claim, dict)
    return claim["done"]


async def _kept_from(
    store: dict[str, object], *, read: timedelta, checked: timedelta, status: OpenPullRequest
) -> None:
    """Leave a status fully read ``read`` ago, its CI last found unchanged ``checked`` ago."""
    _, now = await Topic.last_changed(Topic.PULL_REQUEST_STATE.keyed(_KEY))
    store[_STATUS] = {
        "read_at": (now - read).isoformat(),
        "checked_at": (now - checked).isoformat(),
        "ci": SharedPullRequestStatus._fingerprint(_CI),
        "status": status.model_dump(mode="json"),
    }


def _ci_now(monkeypatch: pytest.MonkeyPatch, ci: CommitCI) -> AsyncMock:
    rest = AsyncMock(return_value=ci)
    monkeypatch.setattr(RepoClient, "commit_ci_over_rest", rest)
    return rest


def _checked_at(store: dict[str, object]) -> object:
    kept = store[_STATUS]
    assert isinstance(kept, dict)
    return kept["checked_at"]


async def test_a_status_whose_ci_has_not_moved_is_kept_without_a_full_read(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read("New"))
    _answers(monkeypatch, load)
    rest = _ci_now(monkeypatch, _CI)
    await _kept_from(
        shared_cache, read=timedelta(minutes=2), checked=timedelta(minutes=2), status=_read()
    )
    checked = _checked_at(shared_cache)

    status = await SharedPullRequestStatus.read(_pull())

    assert status is not None and status.title == "Fix"
    load.assert_not_awaited()
    rest.assert_awaited_once_with("abc123")
    assert _checked_at(shared_cache) != checked
    # Rechecked just now, so the next viewer reads nothing at all.
    assert await SharedPullRequestStatus.read(_pull()) == status
    rest.assert_awaited_once()


async def test_a_status_whose_ci_moved_is_read_again_in_full(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read("New"))
    _answers(monkeypatch, load)
    _ci_now(
        monkeypatch,
        CommitCI([{"name": "test", "status": "completed", "conclusion": "failure"}], []),
    )
    await _kept_from(
        shared_cache, read=timedelta(minutes=2), checked=timedelta(minutes=2), status=_read()
    )

    status = await SharedPullRequestStatus.read(_pull())

    assert status is not None and status.title == "New"
    load.assert_awaited_once()


@pytest.mark.parametrize(
    ("read", "status"),
    [
        (timedelta(minutes=6), _read()),
        # GitHub had not settled whether it merges.
        (timedelta(minutes=2), _read().model_copy(update={"mergeable": None})),
    ],
)
async def test_a_status_too_old_or_unsettled_is_read_again_without_a_recheck(
    registry_db: None,
    shared_cache: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    read: timedelta,
    status: OpenPullRequest,
) -> None:
    load = AsyncMock(return_value=_read("New"))
    _answers(monkeypatch, load)
    rest = _ci_now(monkeypatch, _CI)
    await _kept_from(shared_cache, read=read, checked=timedelta(minutes=2), status=status)

    assert (await SharedPullRequestStatus.read(_pull())) == _read("New")
    load.assert_awaited_once()
    rest.assert_not_awaited()


async def test_a_status_checked_within_a_minute_is_served_as_is(
    registry_db: None, shared_cache: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    load = AsyncMock(return_value=_read("New"))
    _answers(monkeypatch, load)
    rest = _ci_now(monkeypatch, _CI)
    await _kept_from(
        shared_cache, read=timedelta(minutes=4), checked=timedelta(seconds=10), status=_read()
    )

    assert (await SharedPullRequestStatus.read(_pull())) == _read()
    load.assert_not_awaited()
    rest.assert_not_awaited()
