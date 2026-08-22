from collections.abc import Callable
from typing import Any

import pytest
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import usage_reports
from agent.settings import agent_usage

NOW_MS = 1_800_000_000_000


@pytest.fixture
def fake_client(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
    monkeypatch: pytest.MonkeyPatch,
) -> FakeLangGraphClient:
    client = patched_langgraph_client(usage_reports, attr="_client")
    monkeypatch.setattr(agent_usage, "now_ms", lambda: NOW_MS)
    monkeypatch.setattr(usage_reports, "now_ms", lambda: NOW_MS)
    usage_reports._USAGE_CACHE.clear()
    return client


async def _record_run(run_id: str, *, thread_id: str = "thread", login: str = "octo") -> None:
    await agent_usage.record_agent_run_usage(
        run_id=run_id,
        thread_id=thread_id,
        github_login=login,
        user_email=f"{login}@example.com",
        model_id="claude",
        effort=None,
        source="dashboard",
    )


async def test_usage_records_runs_once_and_reads_every_page(
    fake_client: FakeLangGraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_reports, "_PAGE_SIZE", 1)
    await _record_run("run-1", thread_id="shared-thread")
    await _record_run("run-2", thread_id="shared-thread")
    await _record_run("run-1", thread_id="shared-thread")

    payload = await usage_reports.list_agent_usage_leaderboard(
        period="all", limit=10, current_login="octo", current_email="octo@example.com"
    )

    assert payload["rows"][0]["agent_runs"] == 2
    run_searches = [
        kwargs["offset"]
        for method, kwargs in fake_client.calls
        if method == "store.search_items"
        and kwargs["namespace"] == tuple(agent_usage.AGENT_RUN_NAMESPACE)
    ]
    assert run_searches == [0, 1, 2]


async def test_reviewer_stats_use_publication_and_resolution_events(
    fake_client: FakeLangGraphClient,
) -> None:
    finding: dict[str, Any] = {
        "id": "finding-1",
        "status": "open",
        "severity": "high",
        "category": "correctness",
        "first_seen_sha": "bad",
        "last_confirmed_sha": "bad",
        "github_review_comment_id": 1,
        "interactions": [],
    }

    await agent_usage.record_reviewer_publication(
        thread_id="review-thread",
        owner="langchain-ai",
        repo="open-swe",
        pr_number=1,
        head_sha="bad",
        findings=[finding],
    )
    finding.update(status="resolved", last_confirmed_sha="fixed")
    await agent_usage.record_reviewer_finding_state("review-thread", finding)
    payload = await usage_reports.list_agent_usage_leaderboard(
        period="all", limit=10, current_login=None, current_email=None
    )

    stats = payload["reviewer_stats"]
    assert stats["reviewed_prs"] == 1
    assert stats["prs_with_findings"] == 1
    assert stats["surfaced_findings"] == 1
    assert stats["addressed_findings"] == 1
    assert stats["resolved_after_update"] == 1
    assert stats["resolution_rate"] == 1.0


async def test_unknown_finding_state_is_not_recorded(fake_client: FakeLangGraphClient) -> None:
    await agent_usage.record_reviewer_finding_state(
        "review-thread", {"id": "never-published", "status": "resolved"}
    )

    assert fake_client.store.items == {}


async def test_republishing_keeps_the_original_publication_time(
    fake_client: FakeLangGraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_usage, "now_ms", lambda: 1_000)
    await agent_usage.record_reviewer_publication(
        thread_id="review-thread", owner="o", repo="r", pr_number=1, head_sha="sha", findings=[]
    )
    monkeypatch.setattr(agent_usage, "now_ms", lambda: NOW_MS)
    await agent_usage.record_reviewer_publication(
        thread_id="review-thread", owner="o", repo="r", pr_number=1, head_sha="sha", findings=[]
    )

    reviews = await usage_reports._all(agent_usage.REVIEW_NAMESPACE)
    assert [review["published_at_ms"] for review in reviews] == [1_000]


async def test_current_user_row_survives_the_limit(fake_client: FakeLangGraphClient) -> None:
    for index in range(4):
        await _record_run(f"run-{index}", thread_id=f"thread-{index}", login=f"user-{index}")

    kwargs = {"period": "all", "limit": 1, "current_login": "user-3", "current_email": None}
    payload = await usage_reports.list_agent_usage_leaderboard(**kwargs)
    cached = await usage_reports.list_agent_usage_leaderboard(**kwargs)

    for result in (payload, cached):
        assert len(result["rows"]) == 2
        assert result["rows"][-1]["user"]["github_login"] == "user-3"
        assert result["current_user_rank"] == 4


async def test_pr_webhook_updates_a_known_pr_and_ignores_unknown_ones(
    fake_client: FakeLangGraphClient,
) -> None:
    await agent_usage.record_agent_pr_usage(
        thread_id="thread-1",
        github_login="octo",
        user_email=None,
        owner="o",
        repo="r",
        pr_number=1,
        pr_url="https://github.com/o/r/pull/1",
        head="feature",
        base="main",
        additions=1,
        deletions=0,
        changed_files=1,
        created_at="2026-08-01T00:00:00Z",
    )
    payload = {
        "repository": {"name": "r", "owner": {"login": "o"}},
        "pull_request": {
            "number": 1,
            "html_url": "https://github.com/o/r/pull/1",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-08-02T00:00:00Z",
            "additions": 5,
            "deletions": 2,
            "changed_files": 3,
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
        },
    }

    await agent_usage.update_agent_pr_usage_from_webhook(payload)
    await agent_usage.update_agent_pr_usage_from_webhook(
        {**payload, "pull_request": {**payload["pull_request"], "number": 2}}
    )

    prs = await usage_reports._all(agent_usage.AGENT_PR_NAMESPACE)
    assert len(prs) == 1
    assert prs[0]["merged"] is True
    assert prs[0]["state"] == "closed"
    assert prs[0]["merged_at_ms"] == 1_785_628_800_000
    assert prs[0]["created_at_ms"] == 1_785_542_400_000
    assert prs[0]["github_login"] == "octo"
    assert (prs[0]["additions"], prs[0]["deletions"], prs[0]["changed_files"]) == (5, 2, 3)


async def test_legacy_records_are_backfilled_once(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = {
        "thread_id": "legacy-review",
        "created_at": "2026-08-01T00:00:00Z",
        "metadata": {
            "kind": "reviewer",
            "pr": {"owner": "o", "name": "r", "number": 3},
            "last_reviewed_sha": "sha",
            "findings": [{"id": "f_1", "status": "open", "github_review_comment_id": 1}],
        },
    }
    client = patched_langgraph_client(
        usage_reports,
        attr="_client",
        client=FakeLangGraphClient(
            threads=[thread],
            items={
                (tuple(usage_reports.LEGACY_THREAD_NAMESPACE), "t-1"): {
                    "thread_id": "t-1",
                    "github_login": "octo",
                    "model_id": "claude",
                    "source": "dashboard",
                    "created_at_ms": 1_700_000_000_000,
                },
                (tuple(usage_reports.LEGACY_PR_NAMESPACE), "o/r#1"): {
                    "owner": "o",
                    "repo": "r",
                    "pr_number": 1,
                    "github_login": "octo",
                    "merged": True,
                    "additions": 5,
                    "deletions": 2,
                    "created_at_ms": 1_700_000_000_000,
                },
            },
        ),
    )
    monkeypatch.setattr(agent_usage, "now_ms", lambda: NOW_MS)
    monkeypatch.setattr(usage_reports, "now_ms", lambda: NOW_MS)
    usage_reports._USAGE_CACHE.clear()

    payload = await usage_reports.list_agent_usage_leaderboard(
        period="all", limit=10, current_login="octo", current_email=None
    )
    assert payload["rows"][0]["agent_runs"] == 1
    assert payload["rows"][0]["merged_prs"] == 1
    assert payload["reviewer_stats"]["reviewed_prs"] == 1
    assert payload["reviewer_stats"]["surfaced_findings"] == 1
    assert client.store.items[(tuple(usage_reports.BACKFILL_NAMESPACE), "legacy_v1")] == {
        "completed_at_ms": NOW_MS
    }

    usage_reports._USAGE_CACHE.clear()
    await usage_reports.list_agent_usage_leaderboard(
        period="all", limit=10, current_login="octo", current_email=None
    )
    assert len(await usage_reports._all(agent_usage.AGENT_RUN_NAMESPACE)) == 1
    assert len(client.threads.searches) == 1
