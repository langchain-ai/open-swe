"""PostgreSQL regressions for pull requests and their thread/review links."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.github import pull_requests
from agent.github.pull_requests import PullRequest

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "Ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def _pr() -> PullRequest:
    return PullRequest(owner="lc", repo="repo", number=7)


def _client_returning(*pages: list[dict[str, object]]) -> MagicMock:
    client = MagicMock()
    client.threads.search = AsyncMock(side_effect=[*pages, []])
    return client


async def test_linking_never_overwrites_what_save_wrote() -> None:
    await PullRequest(
        owner="lc", repo="repo", number=7, state="merged", title="Add widget", author="ada"
    ).save()
    linked = await _pr().link_thread("fixer")
    reviewed = await _pr().link_review(reviewer_thread_id="rev", github_review_id=11)

    assert (linked.state, linked.title, linked.author) == ("merged", "Add widget", "ada")
    assert (reviewed.state, reviewed.title, reviewed.author) == ("merged", "Add widget", "ada")


async def test_save_from_a_later_event_updates_github_fields_but_keeps_resolves_thread() -> None:
    await PullRequest(
        owner="lc", repo="repo", number=7, title="Add widget", resolves_thread=True
    ).save()
    saved = await PullRequest(
        owner="lc", repo="repo", number=7, state="merged", title="Add widget (final)"
    ).save()

    assert (saved.state, saved.title, saved.resolves_thread) == (
        "merged",
        "Add widget (final)",
        True,
    )
    assert saved.created_at is not None and saved.updated_at is not None


async def test_opening_origin_fills_once_and_survives_later_saves() -> None:
    await PullRequest(owner="lc", repo="repo", number=7, title="From webhook").save()
    await PullRequest(
        owner="lc",
        repo="repo",
        number=7,
        opening_model_id="claude-opus-5-5",
        opening_effort="high",
        langsmith_run_id="run-1",
        slack_team_id="T1",
        slack_channel_id="C1",
        slack_thread_ts="1.0",
        slack_message_ts="1.5",
    ).save()
    await PullRequest(
        owner="lc",
        repo="repo",
        number=7,
        slack_channel_id="C2",
        opening_model_id="other",
        langsmith_run_id="run-2",
    ).save()
    saved = await PullRequest(owner="lc", repo="repo", number=7, state="merged").save()

    assert (
        saved.opening_model_id,
        saved.opening_effort,
        saved.langsmith_run_id,
        saved.slack_team_id,
        saved.slack_channel_id,
        saved.slack_thread_ts,
        saved.slack_message_ts,
    ) == ("claude-opus-5-5", "high", "run-1", "T1", "C1", "1.0", "1.5")


async def test_backfill_promotes_the_oldest_thread_and_skips_reviewer_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/lc/repo/pull/7"
    client = _client_returning(
        [
            {"thread_id": "newer", "metadata": {"pr_url": url}, "created_at": "2026-02-01"},
            {"thread_id": "reviewer", "metadata": {"kind": "reviewer"}, "created_at": "2026-01-01"},
            {"thread_id": "older", "metadata": {"pr_url": url}, "created_at": "2026-01-15"},
        ]
    )
    monkeypatch.setattr(pull_requests, "langgraph_client", lambda: client)

    threads = await (await PullRequest.load("lc", "repo", 7)).linked_threads()

    assert threads == ["older", "newer"]
    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None
    assert stored.primary_thread_id == "older"


async def test_backfill_still_runs_after_a_newer_thread_was_linked_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/lc/repo/pull/7"
    client = _client_returning([{"thread_id": "opener", "metadata": {"pr_url": url}}])
    monkeypatch.setattr(pull_requests, "langgraph_client", lambda: client)

    linked = await _pr().link_thread("commenter", source="github_pr_comment")
    threads = await linked.linked_threads()

    assert set(threads) == {"commenter", "opener"}
    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None and stored.legacy_threads_discovered_at is not None


async def test_failed_legacy_scan_is_retried_on_the_next_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.threads.search = AsyncMock(side_effect=RuntimeError("langgraph down"))
    monkeypatch.setattr(pull_requests, "langgraph_client", lambda: client)

    assert await (await PullRequest.load("lc", "repo", 7)).linked_threads() == []
    searches = client.threads.search.await_count
    assert await (await PullRequest.load("lc", "repo", 7)).linked_threads() == []

    assert client.threads.search.await_count > searches
