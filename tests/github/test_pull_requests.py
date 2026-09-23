"""PostgreSQL regressions for pull requests and their thread/review links."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.github import pull_requests
from agent.github.pull_requests import PullRequest, ThreadLink
from agent.github.repositories import Repository
from agent.users import User

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


async def test_first_linked_thread_is_primary_and_later_ones_are_secondary() -> None:
    await _pr().link_thread("opener")
    saved = await _pr().link_thread("fixer")

    assert saved.primary_thread_id == "opener"
    assert saved.thread_ids == ["opener", "fixer"]


async def test_relinking_a_thread_does_not_duplicate_or_promote_it() -> None:
    await _pr().link_thread("opener")
    await _pr().link_thread("fixer")
    saved = await _pr().link_thread("fixer")

    assert saved.thread_ids == ["opener", "fixer"]
    assert saved.primary_thread_id == "opener"


async def test_the_row_decides_a_links_role_not_the_caller() -> None:
    await _pr().link_thread("opener")
    saved = await PullRequest(
        owner="lc", repo="repo", number=7, threads=[ThreadLink(thread_id="fixer", role="primary")]
    ).save()

    assert [(link.thread_id, link.role) for link in saved.threads] == [
        ("opener", "primary"),
        ("fixer", "secondary"),
    ]


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


async def test_author_links_to_a_registered_user_by_github_id_or_login() -> None:
    ada = await User.sign_in("github", "42", login="Ada")

    by_id = await PullRequest(
        owner="lc", repo="repo", number=1, author="renamed", author_github_id=42
    ).save()
    by_login = await PullRequest(owner="lc", repo="repo", number=2, author="ADA").save()
    unregistered = await PullRequest(
        owner="lc", repo="repo", number=3, author="ghost", author_github_id=99
    ).save()

    assert (by_id.author_user_id, by_login.author_user_id) == (ada.id, ada.id)
    assert (by_id.author_github_id, unregistered.author_github_id) == (42, 99)
    assert unregistered.author_user_id is None


async def test_a_resolved_author_survives_later_saves_and_links() -> None:
    ada = await User.sign_in("github", "42", login="Ada")
    await PullRequest(owner="lc", repo="repo", number=7, author="Ada", author_github_id=42).save()

    relinked = await _pr().link_thread("fixer")
    resaved = await PullRequest(owner="lc", repo="repo", number=7, state="merged").save()

    assert (relinked.author_user_id, resaved.author_user_id) == (ada.id, ada.id)
    assert (relinked.author_github_id, resaved.author_github_id) == (42, 42)


async def test_saving_a_pull_request_registers_its_repository() -> None:
    await PullRequest(owner="LangChain-AI", repo="Open-SWE", number=7).save(repository_private=True)
    await PullRequest(owner="LangChain-AI", repo="Open-SWE", number=8).save()

    repository = await Repository.get("langchain-ai/open-swe")
    assert repository is not None
    assert (repository.full_name, repository.private) == ("LangChain-AI/Open-SWE", True)
    assert [pr.number for pr in await PullRequest.for_repository("langchain-ai", "open-swe")] == [
        7,
        8,
    ]


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


async def test_backfill_runs_once_and_later_reads_use_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://github.com/lc/repo/pull/7"
    client = _client_returning([{"thread_id": "t1", "metadata": {"pr_url": url}}])
    monkeypatch.setattr(pull_requests, "langgraph_client", lambda: client)

    await (await PullRequest.load("lc", "repo", 7)).linked_threads()
    search_calls = client.threads.search.await_count
    await (await PullRequest.load("lc", "repo", 7)).linked_threads()

    assert client.threads.search.await_count == search_calls


async def test_entity_rows_get_synthetic_uuid7_ids_that_survive_resaves() -> None:
    saved = await _pr().link_review(reviewer_thread_id="rev", github_review_id=11)
    resaved = await PullRequest(owner="lc", repo="repo", number=7, title="Retitled").save()
    repository = await Repository.get("lc/repo")

    assert repository is not None
    assert {repository.id.version, saved.id.version, saved.reviews[0].id.version} == {7}
    assert resaved.id == saved.id


async def test_relinking_a_review_updates_the_row_with_the_same_github_id() -> None:
    first = await _pr().link_review(reviewer_thread_id="rev", github_review_id=11, finding_count=3)
    saved = await _pr().link_review(reviewer_thread_id="rev", github_review_id=11, finding_count=1)

    assert [review.github_review_id for review in saved.reviews] == [11]
    assert saved.reviews[0].finding_count == 1
    assert saved.reviews[0].id == first.reviews[0].id
    assert saved.reviews[0].url == "https://github.com/lc/repo/pull/7#pullrequestreview-11"


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


async def test_backfill_leaves_existing_reviews_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/lc/repo/pull/7"
    saved = await _pr().link_review(reviewer_thread_id="rev", github_review_id=11)
    published_at = saved.reviews[0].published_at
    client = _client_returning([{"thread_id": "opener", "metadata": {"pr_url": url}}])
    monkeypatch.setattr(pull_requests, "langgraph_client", lambda: client)

    assert await saved.linked_threads() == ["opener"]

    stored = await PullRequest.get("lc", "repo", 7)
    assert stored is not None
    assert [review.published_at for review in stored.reviews] == [published_at]
