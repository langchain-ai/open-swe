"""Tests for the self-review record, its tools, and the auto-review stand-down."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.github import webhook as github_webhooks
from agent.review.inline_review import (
    CLAIM_GRACE,
    INLINE_REVIEW_NAMESPACE,
    REVIEWS,
    InlineFinding,
    InlineReview,
    format_findings_markdown,
    review_key,
)
from agent.tools import inline_review as inline_review_tools
from agent.webhooks import common as webhook_common
from tests.conftest import FakeStore

THREAD_ID = "11111111-1111-1111-1111-111111111111"


def _configurable(monkeypatch: pytest.MonkeyPatch, thread_id: str = THREAD_ID) -> None:
    monkeypatch.setattr(
        inline_review_tools, "get_config", lambda: {"configurable": {"thread_id": thread_id}}
    )


async def _claim(**overrides: Any) -> InlineReview:
    kwargs: dict[str, Any] = {
        "owner": "LangChain-AI",
        "repo": "Open-SWE",
        "pr_number": 7,
        "pr_url": "https://github.com/langchain-ai/open-swe/pull/7",
        "agent_thread_id": THREAD_ID,
        "base_sha": "b" * 40,
        "head_sha": "h" * 40,
    }
    kwargs.update(overrides)
    return await REVIEWS.claim(**kwargs)


def test_review_key_normalizes_case() -> None:
    assert review_key("LangChain-AI", "Open-SWE", 7) == "langchain-ai/open-swe#7"


@pytest.mark.asyncio
async def test_claim_is_idempotent_and_keeps_findings(fake_store: FakeStore) -> None:
    review = await _claim()
    review.findings.append(InlineFinding(title="first", file="a.py"))
    await REVIEWS.save(review)

    again = await _claim(pr_url="")

    assert again.key == "langchain-ai/open-swe#7"
    assert [finding.title for finding in again.findings] == ["first"]
    assert again.pr_url == "https://github.com/langchain-ai/open-swe/pull/7"
    assert len(fake_store.values(INLINE_REVIEW_NAMESPACE)) == 1


@pytest.mark.asyncio
async def test_claim_from_another_thread_starts_over(fake_store: FakeStore) -> None:
    review = await _claim()
    review.findings.append(InlineFinding(title="from the first thread", file="a.py"))
    await REVIEWS.save(review)

    reclaimed = await _claim(agent_thread_id="a-different-thread")

    assert reclaimed.findings == []
    assert reclaimed.agent_thread_id == "a-different-thread"


@pytest.mark.asyncio
async def test_for_thread_finds_the_claim(fake_store: FakeStore) -> None:
    await _claim()
    await _claim(pr_number=8, agent_thread_id="other-thread")

    mine = await REVIEWS.for_thread(THREAD_ID)

    assert [review.pr_number for review in mine] == [7]


@pytest.mark.asyncio
async def test_record_and_disposition_round_trip(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configurable(monkeypatch)
    await _claim()

    recorded = await inline_review_tools.record_inline_finding(
        severity="high",
        confidence="high",
        category="correctness",
        file="agent/thing.py",
        title="Wrong key breaks the lookup",
        description="`thing` is read back under a different key.",
        start_line=12,
    )
    assert recorded["success"] is True

    listed = await inline_review_tools.list_inline_findings()
    assert listed["count"] == 1
    assert listed["findings"][0]["end_line"] == 12
    assert listed["findings"][0]["disposition"] == "pending"

    disposed = await inline_review_tools.set_inline_finding_disposition(
        recorded["finding_id"], "fixed", "Corrected the key and pushed."
    )
    assert disposed["success"] is True

    review = await REVIEWS.get(review_key("langchain-ai", "open-swe", 7))
    assert review is not None
    assert review.status == "complete"
    assert review.findings[0].disposition == "fixed"
    assert review.findings[0].disposition_note == "Corrected the key and pushed."


@pytest.mark.asyncio
async def test_record_rejects_bad_severity(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configurable(monkeypatch)
    await _claim()

    result = await inline_review_tools.record_inline_finding(
        severity="blocker",
        confidence="high",
        category="correctness",
        file="a.py",
        title="t",
        description="d",
    )

    assert result["success"] is False
    review = await REVIEWS.get(review_key("langchain-ai", "open-swe", 7))
    assert review is not None
    assert review.findings == []


@pytest.mark.asyncio
async def test_tools_report_when_no_pr_is_claimed(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configurable(monkeypatch)

    result = await inline_review_tools.list_inline_findings()

    assert result["success"] is False
    assert "open_pull_request" in result["error"]


@pytest.mark.asyncio
async def test_disposition_rejects_unknown_finding(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configurable(monkeypatch)
    await _claim()

    result = await inline_review_tools.set_inline_finding_disposition("nope", "fixed", "note")

    assert result["success"] is False


def test_markdown_lists_findings_by_severity() -> None:
    review = InlineReview(
        key="o/r#1",
        pr_number=1,
        findings=[
            InlineFinding(
                severity="low",
                title="Log level wrong",
                file="a.py",
                start_line=3,
                created_at="2026-01-01T00:00:00+00:00",
            ),
            InlineFinding(
                severity="critical",
                title="Nil deref on the error path",
                file="b.py",
                start_line=9,
                end_line=11,
                disposition="deferred",
                disposition_note="Needs a decision on the retry policy.",
                created_at="2026-01-01T00:00:01+00:00",
            ),
        ],
    )

    rendered = format_findings_markdown(review)

    assert rendered.index("Nil deref") < rendered.index("Log level wrong")
    assert "`b.py:9-11`" in rendered
    assert "Needs a decision on the retry policy." in rendered


def test_markdown_says_so_when_empty() -> None:
    assert "no findings" in format_findings_markdown(InlineReview(key="o/r#1"))


@pytest.mark.asyncio
async def test_gate_is_true_only_for_a_claimed_pr(fake_store: FakeStore) -> None:
    await _claim()
    repo_config = {"owner": "langchain-ai", "name": "open-swe"}

    assert await webhook_common.inline_review_owns_pr(repo_config, 7) is True
    assert await webhook_common.inline_review_owns_pr(repo_config, 8) is False
    assert await webhook_common.inline_review_owns_pr(repo_config, 0) is False


def test_an_unfinished_claim_expires() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    stale = InlineReview(
        key="o/r#1",
        status="reviewing",
        updated_at=(now - CLAIM_GRACE - timedelta(minutes=1)).isoformat(),
    )
    fresh = InlineReview(key="o/r#1", status="claimed", updated_at=(now).isoformat())
    finished = InlineReview(
        key="o/r#1",
        status="complete",
        updated_at=(now - CLAIM_GRACE * 100).isoformat(),
    )

    assert stale.suppresses_pr_review(now=now) is False
    assert fresh.suppresses_pr_review(now=now) is True
    # A finished self-review owns the PR however long ago it ran.
    assert finished.suppresses_pr_review(now=now) is True


@pytest.mark.asyncio
async def test_gate_releases_a_pr_whose_authoring_run_died(fake_store: FakeStore) -> None:
    review = await _claim()
    review.status = "reviewing"
    await REVIEWS.save(review)
    review.updated_at = (datetime.now(UTC) - CLAIM_GRACE - timedelta(minutes=1)).isoformat()
    await REVIEWS.put(review.key, review)

    owns = await webhook_common.inline_review_owns_pr(
        {"owner": "langchain-ai", "name": "open-swe"}, 7
    )

    assert owns is False


@pytest.mark.asyncio
async def test_gate_reads_false_when_the_store_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(REVIEWS, "get", AsyncMock(side_effect=RuntimeError("store down")))

    owns = await webhook_common.inline_review_owns_pr(
        {"owner": "langchain-ai", "name": "open-swe"}, 7
    )

    assert owns is False


def _pr_ready_payload() -> dict[str, Any]:
    return {
        "action": "opened",
        "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe", "id": 1},
        "pull_request": {
            "number": 7,
            "html_url": "https://github.com/langchain-ai/open-swe/pull/7",
            "title": "T",
            "draft": False,
            "user": {"login": "open-swe[bot]"},
            "head": {"sha": "h" * 40, "ref": "open-swe/x"},
            "base": {"sha": "b" * 40, "ref": "main"},
        },
        "sender": {"login": "open-swe[bot]", "id": 1},
    }


@pytest.mark.asyncio
async def test_auto_review_stands_down_for_a_self_reviewed_pr(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _claim()
    dispatch = AsyncMock()
    monkeypatch.setattr(github_webhooks, "_dispatch_first_review_from_pr_payload", dispatch)

    await github_webhooks.process_github_pr_ready(_pr_ready_payload())

    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_review_still_runs_for_an_unclaimed_pr(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch = AsyncMock()
    monkeypatch.setattr(github_webhooks, "_dispatch_first_review_from_pr_payload", dispatch)
    monkeypatch.setattr(webhook_common, "get_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(webhook_common, "get_team_settings", AsyncMock(return_value={}))

    await github_webhooks.process_github_pr_ready(_pr_ready_payload())

    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_open_pull_request_claims_the_pr(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    import agent.tools.open_pull_request  # noqa: F401

    open_pr = sys.modules["agent.tools.open_pull_request"]

    monkeypatch.setattr(open_pr, "get_config", lambda: {"configurable": {"thread_id": THREAD_ID}})
    monkeypatch.setattr(open_pr, "get_client", MagicMock())

    await open_pr._claim_inline_review(
        owner="langchain-ai",
        repo="open-swe",
        pr={
            "number": 7,
            "html_url": "https://github.com/langchain-ai/open-swe/pull/7",
            "base": {"sha": "b" * 40},
            "head": {"sha": "h" * 40},
        },
    )

    review = await REVIEWS.get(review_key("langchain-ai", "open-swe", 7))
    assert review is not None
    assert review.agent_thread_id == THREAD_ID
    assert review.base_sha == "b" * 40
