"""Pull request outcome and counts recorded as LangSmith thread feedback."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from openswe.analytics import emitter
from openswe.github import pull_requests
from openswe.github.pull_requests import AGENT_OPENED_LINK_SOURCE, PullRequest, ThreadLink
from openswe.webhooks import common as webhook_common

PR_URL = "https://github.com/lc/repo/pull/7"
OPENER = "opener"
FIX = "fix"
COUNTS = {
    "additions": 120,
    "deletions": 30,
    "changed_files": 4,
    "commits": 3,
    "review_comments": 5,
    "comments": 2,
}
COUNT_KEYS = {f"pr_{name}" for name in COUNTS}
MERGE_KEYS = {"pr_merged", f"github_pr_merged:{PR_URL}"}

# Feedback rows by thread, then by key; a later write to the same row replaces it.
Rows = dict[str, dict[str, dict[str, object]]]


def _payload(
    action: str,
    *,
    merged: bool = False,
    author: str = "alice",
    author_id: int = 11,
    sender: dict[str, object] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": action,
        "repository": {"full_name": "lc/repo"},
        "pull_request": {
            "number": 7,
            "state": "open" if action in {"opened", "reopened"} else "closed",
            "merged": merged,
            "merge_commit_sha": "d" * 40 if merged else None,
            "user": {"login": author, "id": author_id},
            **(COUNTS if counts is None else counts),
        },
    }
    if sender is not None:
        payload["sender"] = sender
    return payload


@asynccontextmanager
async def _unlocked(*args: object, **kwargs: object):
    yield


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch) -> Rows:
    rows: Rows = {}

    async def write(thread_id: str, key: str, **kwargs: object) -> bool:
        rows.setdefault(thread_id, {})[key] = kwargs
        return True

    monkeypatch.setattr(webhook_common, "create_langsmith_thread_feedback", write)
    monkeypatch.setattr(pull_requests, "create_langsmith_thread_feedback", write)
    return rows


@pytest.fixture
def link(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Register PR #7 with the given linked threads, each an agent thread tracking it."""
    threads: dict[str, dict[str, object]] = {}

    async def get(thread_id: str) -> dict[str, object]:
        return {"thread_id": thread_id, "metadata": dict(threads[thread_id])}

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        threads[thread_id].update(metadata)

    client = MagicMock()
    client.threads.get = get
    client.threads.update = update
    monkeypatch.setattr(webhook_common, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(webhook_common, "agent_thread_pr_state_lock", _unlocked)
    monkeypatch.setattr(webhook_common, "ask_to_monitor_rollout", AsyncMock())
    monkeypatch.setattr(webhook_common, "subscribe_merged_thread", AsyncMock())
    monkeypatch.setattr(emitter, "task_marked_complete", AsyncMock())
    monkeypatch.setattr(emitter, "task_rework", AsyncMock())

    def register(*links: ThreadLink, opened_by_robin: bool) -> None:
        row = PullRequest(
            owner="lc",
            repo="repo",
            number=7,
            opening_head_sha="a" * 40 if opened_by_robin else "",
            threads=list(links),
        )
        for thread in links:
            threads[thread.thread_id] = {"kind": "agent", "pr_url": PR_URL, "pr_state": "open"}

        async def save(self: PullRequest, **kwargs: object) -> PullRequest:
            return row

        async def linked_threads(self: PullRequest, **kwargs: object) -> list[str]:
            return row.thread_ids

        async def stored(owner: str, repo: str, number: int) -> PullRequest:
            return row

        monkeypatch.setattr(PullRequest, "save", save)
        monkeypatch.setattr(PullRequest, "linked_threads", linked_threads)
        monkeypatch.setattr(PullRequest, "get", stored)

    return register


@pytest.fixture
def robin_pr(link: Callable[..., None]) -> None:
    """A pull request Robin opened from ``OPENER`` that a later ``FIX`` thread also works on."""
    link(
        ThreadLink(thread_id=OPENER, source=AGENT_OPENED_LINK_SOURCE),
        ThreadLink(thread_id=FIX),
        opened_by_robin=True,
    )


@pytest.fixture
def requesters(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[set[str], set[int]]]:
    """Who asked for each Robin-authored pull request, by owner/repo#number."""
    stored: dict[str, tuple[set[str], set[int]]] = {}

    async def lookup(owner: str, repo: str, number: int) -> tuple[set[str], set[int]]:
        return stored.get(f"{owner}/{repo}#{number}", (set(), set()))

    monkeypatch.setattr(PullRequest, "requesters", lookup)
    return stored


async def _deliver(payload: dict[str, object]) -> None:
    await webhook_common.update_agent_thread_pr_state(payload)


def _stats(rows: dict[str, dict[str, object]]) -> dict[str, object]:
    return {key: row["score"] for key, row in rows.items() if key in COUNT_KEYS}


@pytest.mark.usefixtures("robin_pr")
async def test_opened_pr_records_its_counts_on_the_thread_that_opened_it(
    written: Rows,
) -> None:
    await _deliver(_payload("opened", sender={"login": "alice", "id": 11}))

    assert _stats(written[OPENER]) == {
        "pr_additions": 120.0,
        "pr_deletions": 30.0,
        "pr_changed_files": 4.0,
        "pr_commits": 3.0,
        "pr_review_comments": 5.0,
        "pr_comments": 2.0,
    }
    assert {row["event_id"] for row in written[OPENER].values()} == {PR_URL}
    assert written[OPENER].keys() == COUNT_KEYS
    assert FIX not in written


@pytest.mark.usefixtures("robin_pr")
async def test_merged_pr_records_its_merge_on_every_thread_and_counts_on_the_opener(
    written: Rows,
) -> None:
    await _deliver(_payload("closed", merged=True, sender={"login": "carol", "id": 33}))

    assert written[OPENER].keys() == MERGE_KEYS | COUNT_KEYS | {"pr_closed"}
    assert written[OPENER]["pr_closed"]["value"] == "merged"
    assert _stats(written[OPENER])["pr_review_comments"] == 5.0
    assert written[FIX].keys() == MERGE_KEYS


async def test_thread_linked_to_a_pr_robin_did_not_open_gets_only_the_merge(
    written: Rows, link: Callable[..., None]
) -> None:
    link(ThreadLink(thread_id=FIX), opened_by_robin=False)

    await _deliver(_payload("opened", sender={"login": "alice", "id": 11}))
    await _deliver(_payload("closed", sender={"login": "alice", "id": 11}))
    assert FIX not in written

    await _deliver(_payload("reopened", sender={"login": "alice", "id": 11}))
    await _deliver(_payload("closed", merged=True, sender={"login": "alice", "id": 11}))
    assert written[FIX].keys() == MERGE_KEYS


@pytest.mark.usefixtures("robin_pr")
async def test_reopening_and_merging_replace_an_earlier_close(written: Rows) -> None:
    alice = {"login": "alice", "id": 11}

    await _deliver(_payload("closed", sender=alice))
    assert written[OPENER]["pr_closed"]["value"] == "requester"

    await _deliver(_payload("reopened", sender=alice))
    assert written[OPENER]["pr_closed"]["value"] == "reopened"
    assert written[OPENER]["pr_closed"]["event_id"] == PR_URL

    await _deliver(_payload("closed", merged=True, sender={"login": "carol", "id": 33}))
    assert written[OPENER]["pr_closed"]["value"] == "merged"
    assert written[OPENER]["pr_closed"]["score"] is None
    assert "pr_closed" not in written[FIX]


@pytest.mark.parametrize(
    ("author", "author_id", "sender", "asked", "expected"),
    [
        ("alice", 11, {"login": "Alice", "id": 11}, None, "requester"),
        ("alice", 11, {"login": "alice-renamed", "id": 11}, None, "requester"),
        ("alice", 11, {"login": "open-swe[bot]", "id": 99}, None, "robin"),
        ("alice", 11, {"login": "carol", "id": 33}, None, "other"),
        ("open-swe[bot]", 99, {"login": "open-swe[bot]", "id": 99}, None, "robin"),
        ("open-swe[bot]", 99, {"login": "bob", "id": 22}, ({"bob"}, set()), "requester"),
        ("open-swe[bot]", 99, {"login": "bob-renamed", "id": 22}, (set(), {22}), "requester"),
        ("open-swe[bot]", 99, {"login": "carol", "id": 33}, ({"bob"}, {22}), "other"),
        ("open-swe[bot]", 99, {"login": "carol", "id": 33}, None, "unknown"),
        ("alice", 11, None, None, "unknown"),
    ],
)
@pytest.mark.usefixtures("robin_pr")
async def test_pr_closed_without_merge_names_who_closed_it(
    written: Rows,
    requesters: dict[str, tuple[set[str], set[int]]],
    author: str,
    author_id: int,
    sender: dict[str, object] | None,
    asked: tuple[set[str], set[int]] | None,
    expected: str,
) -> None:
    if asked is not None:
        requesters["lc/repo#7"] = asked

    await _deliver(_payload("closed", author=author, author_id=author_id, sender=sender))

    assert written[OPENER]["pr_closed"]["value"] == expected
    assert written[OPENER]["pr_closed"]["score"] is None
    assert written[OPENER]["pr_closed"]["event_id"] == PR_URL
    assert "pr_merged" not in written[OPENER]
    assert _stats(written[OPENER])["pr_additions"] == 120.0


@pytest.mark.usefixtures("robin_pr")
async def test_other_deliveries_record_nothing(written: Rows) -> None:
    await _deliver(_payload("edited", sender={"login": "carol", "id": 33}))

    assert written == {}


@pytest.mark.usefixtures("robin_pr")
async def test_counts_missing_from_the_payload_are_skipped_not_zeroed(written: Rows) -> None:
    await _deliver(
        _payload("closed", counts={"additions": 0, "deletions": 8}, sender={"login": "alice"})
    )

    assert _stats(written[OPENER]) == {"pr_additions": 0.0, "pr_deletions": 8.0}
    assert written[OPENER]["pr_closed"]["value"] == "requester"


@pytest.mark.usefixtures("robin_pr")
async def test_failed_writes_and_lookups_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted: list[tuple[str, str]] = []

    async def unreachable(thread_id: str, key: str, **kwargs: object) -> bool:
        attempted.append((thread_id, key))
        raise RuntimeError("LangSmith unavailable")

    async def no_database(owner: str, repo: str, number: int) -> tuple[set[str], set[int]]:
        raise RuntimeError("PostgreSQL unavailable")

    monkeypatch.setattr(webhook_common, "create_langsmith_thread_feedback", unreachable)
    monkeypatch.setattr(pull_requests, "create_langsmith_thread_feedback", unreachable)
    monkeypatch.setattr(PullRequest, "requesters", no_database)

    await _deliver(_payload("closed", author="open-swe[bot]", sender={"login": "carol", "id": 33}))

    assert set(attempted) == {(OPENER, key) for key in COUNT_KEYS | {"pr_closed"}}


def _reject_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail every registry save; the metadata scan finds a linked thread before the opener."""

    async def rejected(self: PullRequest, **kwargs: object) -> PullRequest:
        raise RuntimeError("PostgreSQL rejected the save")

    async def discovered(self: PullRequest) -> list[str]:
        return [FIX, OPENER]

    monkeypatch.setattr(PullRequest, "save", rejected)
    monkeypatch.setattr(PullRequest, "discover_threads", discovered)


@pytest.mark.usefixtures("robin_pr")
async def test_a_rejected_save_records_counts_on_the_opener_the_stored_row_names(
    written: Rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reject_saves(monkeypatch)

    await _deliver(_payload("closed", sender={"login": "alice", "id": 11}))

    assert written[OPENER].keys() == COUNT_KEYS | {"pr_closed"}
    assert written[OPENER]["pr_closed"]["value"] == "requester"
    assert FIX not in written


@pytest.mark.usefixtures("robin_pr")
async def test_an_unreachable_registry_records_no_pr_feedback_but_still_closes_threads(
    written: Rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reject_saves(monkeypatch)
    monkeypatch.setattr(PullRequest, "get", AsyncMock(side_effect=RuntimeError("unreachable")))

    await _deliver(_payload("closed", sender={"login": "alice", "id": 11}))

    assert written == {}
    threads = webhook_common.get_client(url="").threads
    for thread_id in (OPENER, FIX):
        assert (await threads.get(thread_id))["metadata"]["pr_state"] == "closed"
