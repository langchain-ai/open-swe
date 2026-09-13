from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import reconcile, scheduler


def _run(run_id: str, thread_id: str, age_seconds: float) -> dict[str, Any]:
    created = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "status": "pending",
        "created_at": created.isoformat(),
    }


class _FakeThreads:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self._pages = pages
        self.search_calls: list[dict[str, Any]] = []
        self.update = AsyncMock(return_value=None)

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.search_calls.append(kwargs)
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 100)
        index = offset // limit if limit else 0
        if index < len(self._pages):
            return self._pages[index]
        return []


class _FakeRuns:
    def __init__(self, runs_by_thread: dict[str, Any]) -> None:
        self._runs_by_thread = runs_by_thread
        self.cancel_many = AsyncMock(return_value=None)
        self.list_calls: list[tuple[str, dict[str, Any]]] = []

    async def list(self, thread_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_calls.append((thread_id, kwargs))
        value = self._runs_by_thread.get(thread_id, [])
        if isinstance(value, Exception):
            raise value
        return value


class _FakeClient:
    def __init__(self, threads: _FakeThreads, runs: _FakeRuns) -> None:
        self.threads = threads
        self.runs = runs


def _patch(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(reconcile, "langgraph_client", lambda: client)


def _reviewer_thread(**metadata: Any) -> dict[str, Any]:
    base = {
        "kind": "reviewer",
        "watch": True,
        "last_reviewed_sha": "old-head",
        "pr": {
            "owner": "acme",
            "name": "widget",
            "number": 7,
            "url": "https://github.com/acme/widget/pull/7",
        },
    }
    base.update(metadata)
    return {"thread_id": "reviewer-thread", "metadata": base}


def _live_review_pr(**overrides: Any) -> dict[str, Any]:
    pr = {
        "number": 7,
        "html_url": "https://github.com/acme/widget/pull/7",
        "state": "open",
        "draft": False,
        "base": {
            "sha": "base-head",
            "ref": "main",
            "repo": {"id": 12, "private": True},
        },
        "head": {"sha": "new-head", "ref": "feature"},
    }
    pr.update(overrides)
    return pr


def _patch_reviewer_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    threads: _FakeThreads,
    *,
    live_pr: dict[str, Any] | Exception | None = None,
    enabled: bool = True,
) -> AsyncMock:
    _patch(monkeypatch, _FakeClient(threads, _FakeRuns({})))
    monkeypatch.setattr(
        reconcile.webhook_common, "_is_repo_auto_review_enabled", AsyncMock(return_value=enabled)
    )
    monkeypatch.setattr(
        reconcile.webhook_common,
        "_reviewer_token_for_repo",
        AsyncMock(return_value=("app-token", None)),
    )

    async def fetch(*_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
        if isinstance(live_pr, Exception):
            raise live_pr
        return live_pr or _live_review_pr()

    monkeypatch.setattr(reconcile.webhook_common, "fetch_github_pr_metadata", fetch)
    dispatch = AsyncMock(return_value={"status": "accepted", "ownership": "created"})
    monkeypatch.setattr(reconcile.github_webhook, "process_github_pr_synchronize", dispatch)
    return dispatch


@pytest.mark.asyncio
async def test_reviewer_head_reconcile_dispatches_missed_head(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    threads = _FakeThreads([[_reviewer_thread()]])
    dispatch = _patch_reviewer_reconcile(monkeypatch, threads)

    with caplog.at_level("INFO"):
        counts = await reconcile.reconcile_reviewer_heads()

    assert counts == {"threads_checked": 1, "dispatched": 1, "skipped": 0, "errors": 0}
    payload = dispatch.await_args.args[0]
    assert payload["pull_request"]["head"]["sha"] == "new-head"
    assert payload["repository"] == {
        "owner": {"login": "acme"},
        "name": "widget",
        "private": True,
        "id": 12,
    }
    assert "repository=acme/widget pr=7 head=new-head outcome=created" in caplog.text


@pytest.mark.asyncio
async def test_reviewer_head_reconcile_ignores_old_head_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads(
        [[_reviewer_thread(review_start={"head_sha": "old-head", "status": "started"})]]
    )
    dispatch = _patch_reviewer_reconcile(monkeypatch, threads)

    counts = await reconcile.reconcile_reviewer_heads()

    assert counts["dispatched"] == 1
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"review_start": {"head_sha": "new-head", "status": "claimed"}},
        {
            "head_sha": "new-head",
            "review_check_run_id": 10,
            "current_reviewer_run_id": "run-1",
        },
        {"last_reviewed_sha": "new-head"},
    ],
)
async def test_reviewer_head_reconcile_skips_owned_or_reviewed_head(
    monkeypatch: pytest.MonkeyPatch, metadata: dict[str, Any]
) -> None:
    threads = _FakeThreads([[_reviewer_thread(**metadata)]])
    dispatch = _patch_reviewer_reconcile(monkeypatch, threads)

    counts = await reconcile.reconcile_reviewer_heads()

    assert counts["skipped"] == 1
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("thread", "live_pr", "enabled"),
    [
        (_reviewer_thread(watch=False), _live_review_pr(), True),
        (_reviewer_thread(), _live_review_pr(state="closed"), True),
        (_reviewer_thread(), _live_review_pr(draft=True), True),
        (_reviewer_thread(), _live_review_pr(), False),
    ],
)
async def test_reviewer_head_reconcile_skips_ineligible_pr(
    monkeypatch: pytest.MonkeyPatch,
    thread: dict[str, Any],
    live_pr: dict[str, Any],
    enabled: bool,
) -> None:
    threads = _FakeThreads([[thread]])
    dispatch = _patch_reviewer_reconcile(monkeypatch, threads, live_pr=live_pr, enabled=enabled)

    counts = await reconcile.reconcile_reviewer_heads()

    assert counts["skipped"] == 1
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_reviewer_head_reconcile_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    full_page = [_reviewer_thread(watch=False) for _ in range(reconcile._SEARCH_PAGE_SIZE)]
    threads = _FakeThreads([full_page, [_reviewer_thread()]])
    dispatch = _patch_reviewer_reconcile(monkeypatch, threads)

    counts = await reconcile.reconcile_reviewer_heads()

    assert len(threads.search_calls) == 2
    assert threads.search_calls[0]["metadata"] == {"kind": "reviewer", "watch": True}
    assert threads.search_calls[1]["offset"] == reconcile._SEARCH_PAGE_SIZE
    assert counts["dispatched"] == 1
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_reviewer_head_reconcile_isolates_pr_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _reviewer_thread()
    good = _reviewer_thread(
        pr={
            "owner": "acme",
            "name": "widget",
            "number": 8,
            "url": "https://github.com/acme/widget/pull/8",
        }
    )
    good["thread_id"] = "reviewer-thread-8"
    threads = _FakeThreads([[bad, good]])
    _patch(monkeypatch, _FakeClient(threads, _FakeRuns({})))
    monkeypatch.setattr(
        reconcile.webhook_common, "_is_repo_auto_review_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        reconcile.webhook_common,
        "_reviewer_token_for_repo",
        AsyncMock(return_value=("app-token", None)),
    )

    async def fetch(pr_ref: Any, **_kwargs: Any) -> dict[str, Any]:
        if pr_ref.number == 7:
            raise RuntimeError("GitHub unavailable")
        return _live_review_pr(number=8)

    monkeypatch.setattr(reconcile.webhook_common, "fetch_github_pr_metadata", fetch)
    dispatch = AsyncMock(return_value={"status": "accepted", "ownership": "created"})
    monkeypatch.setattr(reconcile.github_webhook, "process_github_pr_synchronize", dispatch)

    counts = await reconcile.reconcile_reviewer_heads()

    assert counts["errors"] == 1
    assert counts["dispatched"] == 1
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancels_only_stale_pending_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "t1"}]])
    runs = _FakeRuns(
        {
            "t1": [
                _run("old1", "t1", age_seconds=4000),
                _run("fresh1", "t1", age_seconds=60),
                _run("old2", "t1", age_seconds=10000),
            ]
        }
    )
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 2, "cancelled": 2}
    runs.cancel_many.assert_awaited_once()
    assert runs.cancel_many.await_args is not None
    kwargs = runs.cancel_many.await_args.kwargs
    assert kwargs["thread_id"] == "t1"
    assert sorted(kwargs["run_ids"]) == ["old1", "old2"]


@pytest.mark.asyncio
async def test_no_stale_runs_means_no_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "t1"}]])
    runs = _FakeRuns({"t1": [_run("fresh1", "t1", age_seconds=30)]})
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 0, "cancelled": 0}
    runs.cancel_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_bad_thread_does_not_abort_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "bad"}, {"thread_id": "good"}]])
    runs = _FakeRuns(
        {
            "bad": RuntimeError("runs.list exploded"),
            "good": [_run("old1", "good", age_seconds=5000)],
        }
    )
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    # Both threads counted; the good thread is still reconciled despite the bad one.
    assert counts == {"threads_checked": 2, "stale_runs": 1, "cancelled": 1}
    runs.cancel_many.assert_awaited_once()
    assert runs.cancel_many.await_args is not None
    assert runs.cancel_many.await_args.kwargs["thread_id"] == "good"
    assert runs.cancel_many.await_args.kwargs["run_ids"] == ["old1"]


@pytest.mark.asyncio
async def test_paginates_busy_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    full_page = [{"thread_id": f"t{i}"} for i in range(reconcile._SEARCH_PAGE_SIZE)]
    second_page = [{"thread_id": "tail"}]
    threads = _FakeThreads([full_page, second_page])
    runs_by_thread: dict[str, Any] = {t["thread_id"]: [] for t in full_page}
    runs_by_thread["tail"] = [_run("old", "tail", age_seconds=9000)]
    runs = _FakeRuns(runs_by_thread)
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts["threads_checked"] == reconcile._SEARCH_PAGE_SIZE + 1
    assert counts["cancelled"] == 1
    # Two search calls: first full page triggers a second page fetch.
    assert len(threads.search_calls) == 2
    assert threads.search_calls[0]["offset"] == 0
    assert threads.search_calls[1]["offset"] == reconcile._SEARCH_PAGE_SIZE
    assert threads.search_calls[0]["status"] == "busy"


@pytest.mark.asyncio
async def test_unparseable_created_at_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    threads = _FakeThreads([[{"thread_id": "t1"}]])
    runs = _FakeRuns(
        {
            "t1": [
                {
                    "run_id": "bad",
                    "thread_id": "t1",
                    "status": "pending",
                    "created_at": "not-a-date",
                },
                _run("old", "t1", age_seconds=5000),
            ]
        }
    )
    _patch(monkeypatch, _FakeClient(threads, runs))

    counts = await reconcile.reconcile_stale_runs(max_age_seconds=1800)

    assert counts == {"threads_checked": 1, "stale_runs": 1, "cancelled": 1}
    assert runs.cancel_many.await_args is not None
    assert runs.cancel_many.await_args.kwargs["run_ids"] == ["old"]


def _auto_merge_thread(**metadata: Any) -> dict[str, Any]:
    base = {
        "pr_owner": "acme",
        "pr_repo": "widget",
        "pr_number": 7,
        "auto_merge_intent": True,
        "auto_merge_reconcile": True,
        "auto_merge_phase": "pending",
        "auto_merge_phase_at": datetime.now(UTC).isoformat(),
        "auto_merge_head_sha": "",
    }
    base.update(metadata)
    return {"thread_id": "agent-thread", "metadata": base}


def _pr_data(**overrides: Any) -> dict[str, Any]:
    pr = {
        "id": "PR_1",
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "headRefOid": "abc123",
        "labels": {"nodes": []},
    }
    pr.update(overrides)
    return {"repository": {"defaultBranchRef": {"name": "main"}, "pullRequest": pr}}


def _check(
    name: str,
    *,
    head_sha: str = "abc123",
    status: str = "completed",
    conclusion: str | None = "success",
    app: str = "mergify",
) -> dict[str, Any]:
    return {
        "name": name,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": app},
    }


def _checks(
    *,
    head_sha: str = "abc123",
    protections_status: str = "completed",
    protections_conclusion: str | None = "success",
    queue_status: str = "completed",
    queue_conclusion: str | None = "neutral",
) -> dict[str, dict[str, Any]]:
    return {
        reconcile._MERGIFY_PROTECTIONS_CHECK: _check(
            reconcile._MERGIFY_PROTECTIONS_CHECK,
            head_sha=head_sha,
            status=protections_status,
            conclusion=protections_conclusion,
        ),
        reconcile._MERGIFY_QUEUE_CHECK: _check(
            reconcile._MERGIFY_QUEUE_CHECK,
            head_sha=head_sha,
            status=queue_status,
            conclusion=queue_conclusion,
        ),
    }


@asynccontextmanager
async def _fake_github_client(**_kwargs: Any):
    yield object()


async def _coro(value: Any) -> Any:
    return value


def _patch_auto_merge(
    monkeypatch: pytest.MonkeyPatch,
    threads: _FakeThreads,
    *,
    pr: dict[str, Any] | None = None,
    checks: dict[str, dict[str, Any]] | Exception | None = None,
) -> list[str]:
    _patch(monkeypatch, _FakeClient(threads, _FakeRuns({})))
    monkeypatch.setattr(reconcile, "github_client", _fake_github_client)
    monkeypatch.setattr(
        reconcile, "get_github_app_installation_token", lambda **_kw: _coro("token")
    )
    queries: list[str] = []

    async def fake_graphql(_client: Any, query: str, _variables: dict[str, Any]):
        queries.append(query)
        return pr or _pr_data()

    async def fake_checks(*_args: Any, **_kwargs: Any) -> dict[str, dict[str, Any]]:
        if isinstance(checks, Exception):
            raise checks
        return checks or _checks()

    monkeypatch.setattr(reconcile, "_graphql", fake_graphql)
    monkeypatch.setattr(reconcile, "_mergify_checks", fake_checks)
    return queries


def _last_phase(threads: _FakeThreads) -> str:
    await_args = threads.update.await_args
    assert await_args is not None
    return await_args.kwargs["metadata"]["auto_merge_phase"]


@pytest.mark.asyncio
async def test_auto_merge_observes_mergify_enqueue_on_exact_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread()]])
    queries = _patch_auto_merge(monkeypatch, threads)

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["enqueue"] == 1
    assert _last_phase(threads) == "enqueue"
    assert queries == [reconcile._AUTO_MERGE_QUERY]


@pytest.mark.asyncio
async def test_auto_merge_awaits_mergify_checks_on_exact_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread()]])
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(protections_status="in_progress", protections_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["awaiting_checks"] == 1
    assert _last_phase(threads) == "awaiting_checks"


@pytest.mark.asyncio
async def test_auto_merge_observes_mergify_queue_on_exact_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread()]])
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queued"] == 1
    assert _last_phase(threads) == "queued"


@pytest.mark.asyncio
async def test_auto_merge_alerts_when_queue_dwell_exceeds_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_since = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    threads = _FakeThreads(
        [
            [
                _auto_merge_thread(
                    auto_merge_phase="queued",
                    auto_merge_phase_since=phase_since,
                    auto_merge_head_sha="abc123",
                )
            ]
        ]
    )
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queue_stalled"] == 1
    await_args = threads.update.await_args
    assert await_args is not None
    written = await_args.kwargs["metadata"]
    assert written["auto_merge_phase_since"] == phase_since
    assert written["auto_merge_alert_reason"] == "queue_stall_in_queue"
    assert written["auto_merge_alert_at"]


@pytest.mark.asyncio
async def test_auto_merge_does_not_alert_for_recent_queue_dwell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_since = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    threads = _FakeThreads(
        [
            [
                _auto_merge_thread(
                    auto_merge_phase="queued",
                    auto_merge_phase_since=phase_since,
                    auto_merge_head_sha="abc123",
                )
            ]
        ]
    )
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queue_stalled"] == 0
    await_args = threads.update.await_args
    assert await_args is not None
    written = await_args.kwargs["metadata"]
    assert written["auto_merge_phase_since"] == phase_since
    assert "auto_merge_alert_reason" not in written
    assert "auto_merge_alert_at" not in written


@pytest.mark.asyncio
@pytest.mark.parametrize("seeded_alert_reason", ["", "queue_stall_in_queue"])
async def test_auto_merge_resets_queue_dwell_when_head_changes(
    monkeypatch: pytest.MonkeyPatch,
    seeded_alert_reason: str,
) -> None:
    old_phase_since = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()
    thread_metadata: dict[str, Any] = {
        "auto_merge_phase": "queued",
        "auto_merge_phase_since": old_phase_since,
        "auto_merge_head_sha": "def456",
    }
    if seeded_alert_reason:
        thread_metadata["auto_merge_alert_reason"] = seeded_alert_reason
    threads = _FakeThreads([[_auto_merge_thread(**thread_metadata)]])
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queue_stalled"] == 0
    await_args = threads.update.await_args
    assert await_args is not None
    written = await_args.kwargs["metadata"]
    if seeded_alert_reason:
        assert written["auto_merge_alert_reason"] == ""
    else:
        assert "auto_merge_alert_reason" not in written
    assert "auto_merge_alert_at" not in written
    assert written["auto_merge_phase_since"] == written["auto_merge_phase_at"]
    assert written["auto_merge_phase_since"] != old_phase_since
    assert written["auto_merge_head_sha"] == "abc123"


@pytest.mark.asyncio
async def test_auto_merge_starts_queue_dwell_on_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_phase_since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    threads = _FakeThreads(
        [
            [
                _auto_merge_thread(
                    auto_merge_phase="awaiting_checks",
                    auto_merge_phase_since=old_phase_since,
                )
            ]
        ]
    )
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queue_stalled"] == 0
    await_args = threads.update.await_args
    assert await_args is not None
    written = await_args.kwargs["metadata"]
    assert written["auto_merge_phase_since"] == written["auto_merge_phase_at"]
    assert written["auto_merge_phase_since"] != old_phase_since
    assert "auto_merge_alert_reason" not in written
    assert "auto_merge_alert_at" not in written


@pytest.mark.asyncio
async def test_auto_merge_repairs_missing_queue_phase_since_without_alerting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread(auto_merge_phase="queued")]])
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queue_stalled"] == 0
    await_args = threads.update.await_args
    assert await_args is not None
    written = await_args.kwargs["metadata"]
    assert written["auto_merge_phase_since"] == written["auto_merge_phase_at"]
    assert "auto_merge_alert_reason" not in written


@pytest.mark.asyncio
async def test_auto_merge_clears_queue_alert_when_phase_changes_to_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads(
        [
            [
                _auto_merge_thread(
                    auto_merge_phase="queued",
                    auto_merge_phase_since=(datetime.now(UTC) - timedelta(minutes=20)).isoformat(),
                    auto_merge_alert_reason="queue_stall_in_queue",
                )
            ]
        ]
    )
    _patch_auto_merge(monkeypatch, threads)

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["enqueue"] == 1
    await_args = threads.update.await_args
    assert await_args is not None
    assert await_args.kwargs["metadata"]["auto_merge_alert_reason"] == ""


@pytest.mark.asyncio
async def test_auto_merge_omits_queue_alert_clear_when_no_alert_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread(auto_merge_phase="queued")]])
    _patch_auto_merge(monkeypatch, threads)

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["enqueue"] == 1
    await_args = threads.update.await_args
    assert await_args is not None
    assert "auto_merge_alert_reason" not in await_args.kwargs["metadata"]


@pytest.mark.asyncio
async def test_auto_merge_queue_stall_threshold_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_MERGE_QUEUE_STALL_MINUTES", "1")
    threads = _FakeThreads(
        [
            [
                _auto_merge_thread(
                    auto_merge_phase="queued",
                    auto_merge_phase_since=(datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
                    auto_merge_head_sha="abc123",
                )
            ]
        ]
    )
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queue_stalled"] == 1
    await_args = threads.update.await_args
    assert await_args is not None
    assert await_args.kwargs["metadata"]["auto_merge_alert_reason"] == "queue_stall_in_queue"


@pytest.mark.asyncio
async def test_auto_merge_queue_stall_threshold_invalid_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_MERGE_QUEUE_STALL_MINUTES", "not-a-number")
    threads = _FakeThreads(
        [
            [
                _auto_merge_thread(
                    auto_merge_phase="queued",
                    auto_merge_phase_since=(datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
                    auto_merge_head_sha="abc123",
                )
            ]
        ]
    )
    _patch_auto_merge(
        monkeypatch,
        threads,
        checks=_checks(queue_status="in_progress", queue_conclusion=None),
    )

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["queue_stalled"] == 0
    await_args = threads.update.await_args
    assert await_args is not None
    assert "auto_merge_alert_reason" not in await_args.kwargs["metadata"]


@pytest.mark.asyncio
async def test_auto_merge_records_merged_pr_without_reading_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads(
        [
            [
                _auto_merge_thread(
                    auto_merge_phase="queued",
                    auto_merge_alert_reason="queue_stall_in_queue",
                )
            ]
        ]
    )
    checks = AsyncMock()
    _patch_auto_merge(monkeypatch, threads, pr=_pr_data(state="MERGED"))
    monkeypatch.setattr(reconcile, "_mergify_checks", checks)

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["merged"] == 1
    assert _last_phase(threads) == "merged"
    await_args = threads.update.await_args
    assert await_args is not None
    assert await_args.kwargs["metadata"]["auto_merge_reconcile"] is False
    assert await_args.kwargs["metadata"]["auto_merge_alert_reason"] == ""
    checks.assert_not_awaited()


@pytest.mark.parametrize(
    "checks",
    [RuntimeError("Mergify unavailable"), _checks(head_sha="old-head")],
    ids=["outage", "stale-head"],
)
@pytest.mark.asyncio
async def test_auto_merge_hold_returns_exact_head_pr_to_draft_before_mergify_state(
    monkeypatch: pytest.MonkeyPatch,
    checks: dict[str, dict[str, Any]] | Exception,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread(merge_hold_requested=True)]])
    queries = _patch_auto_merge(monkeypatch, threads, checks=checks)

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["held"] == 1
    assert counts["held_drafted"] == 1
    assert counts["backend_unavailable"] == 0
    assert counts["stale_head"] == 0
    assert _last_phase(threads) == "held"
    assert queries == [reconcile._AUTO_MERGE_QUERY, reconcile._CONVERT_TO_DRAFT]


@pytest.mark.asyncio
async def test_auto_merge_stale_mergify_head_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread()]])
    queries = _patch_auto_merge(monkeypatch, threads, checks=_checks(head_sha="old-head"))

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["stale_head"] == 1
    assert _last_phase(threads) == "stale_head"
    assert queries == [reconcile._AUTO_MERGE_QUERY]


@pytest.mark.asyncio
async def test_auto_merge_mergify_outage_fails_closed_and_recovers_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads = _FakeThreads([[_auto_merge_thread()]])
    queries = _patch_auto_merge(monkeypatch, threads, checks=RuntimeError("Mergify unavailable"))

    counts = await reconcile.reconcile_auto_merge_prs()

    assert counts["backend_unavailable"] == 1
    assert _last_phase(threads) == "backend_unavailable"
    assert queries == [reconcile._AUTO_MERGE_QUERY]
    await_args = threads.update.await_args
    assert await_args is not None
    assert "auto_merge_reconcile" not in await_args.kwargs["metadata"]


@pytest.mark.asyncio
async def test_mergify_checks_require_mergify_app_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "check_runs": [
                    _check(reconcile._MERGIFY_PROTECTIONS_CHECK, app="other-app"),
                    _check(reconcile._MERGIFY_QUEUE_CHECK),
                ]
            }

    monkeypatch.setattr(reconcile, "github_request", lambda *_a, **_kw: _coro(Response()))

    client: Any = object()
    checks = await reconcile._mergify_checks(client, "acme", "widget", "abc123")

    assert reconcile._MERGIFY_PROTECTIONS_CHECK not in checks
    assert reconcile._mergify_state(checks, "abc123") == "backend_unavailable"


def test_auto_merge_reconciler_has_no_native_queue_state_or_mutations() -> None:
    source = reconcile._AUTO_MERGE_QUERY + reconcile._CONVERT_TO_DRAFT

    assert "isInMergeQueue" not in source
    assert "enablePullRequestAutoMerge" not in source
    assert "disablePullRequestAutoMerge" not in source
    assert "dequeuePullRequest" not in source


@pytest.mark.asyncio
async def test_scheduler_reconcile_runs_all_sweeps(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = AsyncMock(return_value={"cancelled": 1})
    auto_merge = AsyncMock(return_value={"queued": 1})
    reviewer_heads = AsyncMock(return_value={"dispatched": 1})
    monkeypatch.setattr(scheduler, "reconcile_stale_runs", stale)
    monkeypatch.setattr(scheduler, "reconcile_auto_merge_prs", auto_merge)
    monkeypatch.setattr(scheduler, "reconcile_reviewer_heads", reviewer_heads)

    result = await scheduler._launch({"task": "reconcile"}, {})

    assert result == {
        "result": {
            "stale_runs": {"cancelled": 1},
            "auto_merge": {"queued": 1},
            "reviewer_heads": {"dispatched": 1},
        }
    }
    stale.assert_awaited_once()
    auto_merge.assert_awaited_once()
    reviewer_heads.assert_awaited_once()
