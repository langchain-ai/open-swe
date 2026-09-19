import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

from agent.review import risk
from tests.conftest import FakeStore


async def assessment(run_id: str = "run") -> risk.RiskAssessment:
    record = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id=run_id,
        assessment=risk.RiskInput(
            head_sha="a" * 40, score=1, confidence="high", rationale="Docs only."
        ),
        findings=[],
    )
    record.github_review_id = 123
    record.publication_complete = True
    return await risk.ASSESSMENTS.put(record.id, record)


def page(
    record: risk.RiskAssessment, votes: list[tuple[str | None, str]], *, more: bool = False
) -> dict[str, object]:
    return {
        "data": {
            "node": {
                "databaseId": 123,
                "body": f"<!-- open-swe-review-risk id={record.id} -->",
                "commit": {"oid": record.head_sha},
                "reactions": {
                    "nodes": [
                        {"content": content, "user": {"login": login} if login else None}
                        for login, content in votes
                    ],
                    "pageInfo": {"hasNextPage": more, "endCursor": "next" if more else None},
                },
            }
        }
    }


@pytest.fixture(autouse=True)
def database_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.review import reactions

    lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction() -> AsyncIterator[AsyncMock]:
        async with lock:
            connection = AsyncMock()
            connection.execute.return_value = MagicMock(scalar_one=lambda: True)
            yield connection

    monkeypatch.setattr(reactions.database, "transaction", transaction)


@pytest.fixture
def github() -> Iterator[AsyncMock]:
    get = httpx2.Response(
        200,
        json={"node_id": "PRR_123"},
        request=httpx2.Request("GET", "https://api.github.com/test"),
    )
    with (
        patch("agent.review.reactions.github_client", return_value=AsyncMock()),
        patch("agent.review.reactions.github_request", AsyncMock(return_value=get)) as request,
    ):
        yield request


def response(value: dict[str, object]) -> httpx2.Response:
    return httpx2.Response(
        200, json=value, request=httpx2.Request("POST", "https://api.github.com/graphql")
    )


async def test_comment_only_context_does_not_invent_an_approval_decision(
    fake_store: FakeStore,
) -> None:
    record = await assessment()
    saved = await risk.save_feedback(
        record, login="Alice", submission=risk.RiskFeedbackSubmission(comment="Risk is overstated.")
    )
    assert saved.decision is None
    assert saved.comment == "Risk is overstated."
    with pytest.raises(ValueError):
        risk.RiskFeedbackSubmission(comment="   ")


async def test_reactions_page_per_person_and_ignore_bots_other_emojis_and_conflicts(
    fake_store: FakeStore, github: AsyncMock
) -> None:
    from agent.review import reactions

    record = await assessment()
    github.side_effect = [
        github.return_value,
        response(
            page(
                record,
                [
                    ("Alice", "THUMBS_UP"),
                    ("bot[bot]", "THUMBS_DOWN"),
                    (None, "THUMBS_UP"),
                    ("Charlie", "HEART"),
                ],
                more=True,
            )
        ),
        response(
            page(
                record,
                [
                    ("alice", "THUMBS_UP"),
                    ("Bob", "THUMBS_UP"),
                    ("Bob", "THUMBS_DOWN"),
                    ("Dana", "THUMBS_DOWN"),
                ],
            )
        ),
    ]
    await reactions.sync_assessment_reactions(record, "test-token")
    result = await reactions.get_reaction_summary(record.id, "ALICE")
    assert (result.helpful, result.unhelpful, result.viewer_rating) == (1, 1, "helpful")
    assert (await reactions.get_reaction_summary(record.id, "bob")).viewer_rating == "conflicting"
    assert result.synced_at is not None
    assert github.call_args.kwargs["json"]["variables"]["cursor"] == "next"


async def test_removed_reactions_clear_votes_without_overwriting_written_feedback(
    fake_store: FakeStore, github: AsyncMock
) -> None:
    from agent.review import reactions

    record = await assessment()
    await risk.save_feedback(
        record,
        login="alice",
        submission=risk.RiskFeedbackSubmission(decision="needs_review", comment="Contract change"),
    )
    github.side_effect = [
        github.return_value,
        response(page(record, [("alice", "THUMBS_UP")])),
        github.return_value,
        response(page(record, [])),
    ]
    await reactions.sync_assessment_reactions(record, "token")
    await reactions.sync_assessment_reactions(record, "token")
    result = await reactions.get_reaction_summary(record.id, "alice")
    assert (result.helpful, result.viewer_rating) == (0, None)
    assert (await risk.get_feedback(record.id, "alice")).decision == "needs_review"
    assert (await risk.ASSESSMENTS.get(record.id)).score == 1


async def test_failed_pagination_keeps_previous_complete_snapshot(
    fake_store: FakeStore, github: AsyncMock
) -> None:
    from agent.review import reactions

    record = await assessment()
    github.side_effect = [github.return_value, response(page(record, [("alice", "THUMBS_UP")]))]
    await reactions.sync_assessment_reactions(record, "token")
    original = await reactions.get_reaction_summary(record.id, "alice")
    github.side_effect = [
        github.return_value,
        response(page(record, [], more=True)),
        response({"data": None, "errors": [{"message": "Rate limited"}]}),
    ]
    with pytest.raises(ValueError):
        await reactions.sync_assessment_reactions(record, "token")
    assert await reactions.get_reaction_summary(record.id, "alice") == original


async def test_votes_are_bound_to_the_published_assessment_not_the_latest_head(
    fake_store: FakeStore, github: AsyncMock
) -> None:
    from agent.review import reactions

    old = await assessment("old")
    latest = await assessment("new")
    wrong = old.model_copy(update={"head_sha": "b" * 40})
    github.side_effect = [github.return_value, response(page(wrong, [("alice", "THUMBS_UP")]))]
    with pytest.raises(ValueError):
        await reactions.sync_assessment_reactions(old, "token")
    github.side_effect = [github.return_value, response(page(old, [("alice", "THUMBS_UP")]))]
    await reactions.sync_assessment_reactions(old, "token")
    assert (await reactions.get_reaction_summary(old.id, "alice")).helpful == 1
    assert (await reactions.get_reaction_summary(latest.id, "alice")).helpful == 0


async def test_scheduler_advances_past_failed_reviews_and_wraps_to_reconcile_removals(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent import scheduler
    from agent.review import reactions

    first = await assessment("first")
    second = await assessment("second")
    monkeypatch.setattr(reactions, "BATCH_SIZE", 1)
    monkeypatch.setattr(
        reactions, "get_github_app_installation_id_for_repo", AsyncMock(return_value=7)
    )
    monkeypatch.setattr(
        reactions, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    visited: list[str] = []

    async def sync(record: risk.RiskAssessment, token: str) -> None:
        visited.append(record.id)
        if record.id == first.id:
            raise ValueError("Unavailable review")

    monkeypatch.setattr(reactions, "sync_assessment_reactions", sync)
    graph = scheduler.get_scheduler()
    assert (await graph.ainvoke({"task": reactions.CRON_TASK}))["result"] == {
        "synced": 0,
        "failed": 1,
    }
    assert (await graph.ainvoke({"task": reactions.CRON_TASK}))["result"] == {
        "synced": 1,
        "failed": 0,
    }
    await graph.ainvoke({"task": reactions.CRON_TASK})
    await graph.ainvoke({"task": reactions.CRON_TASK})
    assert visited == [first.id, second.id, first.id]


async def test_repeated_publications_share_one_reaction_collection_cron() -> None:
    from agent.review import reactions

    registered: list[dict[str, str]] = []

    async def search(**kwargs: object) -> list[dict[str, str]]:
        snapshot = list(registered)
        await asyncio.sleep(0)
        return snapshot

    async def create(*args: object, **kwargs: object) -> dict[str, str]:
        cron = {"cron_id": "one-collector"}
        registered.append(cron)
        return cron

    client = MagicMock()
    client.crons.search = AsyncMock(side_effect=search)
    client.crons.create = AsyncMock(side_effect=create)
    with patch.object(reactions, "get_client", return_value=client):
        await asyncio.gather(
            reactions.ensure_reaction_sync_cron(), reactions.ensure_reaction_sync_cron()
        )
    assert len(registered) == 1


async def test_startup_retries_failed_registration_without_another_publication() -> None:
    from agent.review import reactions

    recovered = asyncio.Event()
    attempts = 0

    async def register() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("Cron service unavailable")
        recovered.set()

    with (
        patch.object(reactions, "ensure_reaction_sync_cron", side_effect=register),
        patch.object(reactions, "REGISTRATION_INTERVAL_SECONDS", 0.01),
    ):
        async with reactions.reaction_sync_lifecycle():
            await asyncio.wait_for(recovered.wait(), timeout=1)
        stopped_at = attempts
        await asyncio.sleep(0.02)
    assert attempts == stopped_at == 2
