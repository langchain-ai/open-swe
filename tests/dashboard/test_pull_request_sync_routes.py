"""The manual pull request sync endpoints: who may call them and what they return."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.dashboard import oauth
from agent.github import pull_request_sync_routes
from agent.github.pull_request_sweep import RepositorySweep, SweepReport
from agent.github.pull_requests import PullRequest, PullRequestCheck, PullRequestReviewThread

HEAD_SHA = "a" * 40
SYNCED_AT = datetime(2026, 9, 15, tzinfo=UTC)
PULL_REQUEST_PATH = "/github/pull-requests/lc/repo/7/sync"
REPOSITORY_PATH = "/github/repositories/lc/repo/sync"
STALE_PATH = "/github/pull-requests/sync-stale"


def _client(monkeypatch: pytest.MonkeyPatch, *, login: str) -> TestClient:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")
    app = FastAPI()
    app.include_router(pull_request_sync_routes.router)
    app.dependency_overrides[oauth.require_session] = lambda: {
        "sub": login,
        "email": f"{login}@example.com",
    }
    return TestClient(app)


def _row() -> PullRequest:
    row = PullRequest(
        owner="lc",
        repo="repo",
        number=7,
        state="open",
        head_sha=HEAD_SHA,
        mergeable_state="clean",
    )
    row.checks.append(
        PullRequestCheck(head_sha=HEAD_SHA, kind="check_run", external_id="1", name="ci")
    )
    row.checks.append(
        PullRequestCheck(head_sha="b" * 40, kind="check_run", external_id="1", name="ci")
    )
    row.review_threads.append(PullRequestReviewThread(node_id="t1"))
    row.review_threads.append(PullRequestReviewThread(node_id="t2", is_resolved=True))
    row.last_synced_at = SYNCED_AT
    return row


@pytest.mark.parametrize("path", [PULL_REQUEST_PATH, REPOSITORY_PATH, STALE_PATH])
def test_non_admins_cannot_force_a_sync(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    response = _client(monkeypatch, login="mallory").post(path)

    assert response.status_code == 403


def test_pull_request_sync_returns_the_stored_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pull_request_sync_routes,
        "get_github_app_installation_token",
        AsyncMock(return_value="installation-token"),
    )
    sync = AsyncMock(return_value=_row())
    monkeypatch.setattr(pull_request_sync_routes, "sync_pull_request", sync)

    response = _client(monkeypatch, login="alice").post(PULL_REQUEST_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "repo_full_name": "lc/repo",
        "number": 7,
        "state": "open",
        "draft": False,
        "head_sha": HEAD_SHA,
        "mergeable_state": "clean",
        "check_count": 1,
        "unresolved_review_thread_count": 1,
        "last_synced_at": "2026-09-15T00:00:00Z",
    }
    assert sync.await_args.args == ("lc", "repo", 7)
    assert sync.await_args.kwargs == {"token": "installation-token"}


def test_pull_request_sync_reports_an_unreadable_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pull_request_sync_routes,
        "get_github_app_installation_token",
        AsyncMock(return_value="installation-token"),
    )
    monkeypatch.setattr(pull_request_sync_routes, "sync_pull_request", AsyncMock(return_value=None))

    response = _client(monkeypatch, login="alice").post(PULL_REQUEST_PATH)

    assert response.status_code == 502


def test_malformed_repository_names_never_reach_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep = AsyncMock()
    monkeypatch.setattr(pull_request_sync_routes, "sweep_repository", sweep)

    response = _client(monkeypatch, login="alice").post("/github/repositories/lc~bad/repo/sync")

    assert response.status_code == 404
    sweep.assert_not_awaited()


def test_repository_sync_returns_the_sweep_report(monkeypatch: pytest.MonkeyPatch) -> None:
    report = SweepReport(synced=2, failed=1)
    report.repositories.append(RepositorySweep(repo_full_name="lc/repo", synced=2, failed=1))
    sweep = AsyncMock(return_value=report)
    monkeypatch.setattr(pull_request_sync_routes, "sweep_repository", sweep)

    response = _client(monkeypatch, login="alice").post(REPOSITORY_PATH)

    assert response.status_code == 200
    assert response.json() == {
        "synced": 2,
        "failed": 1,
        "skipped": 0,
        "repositories": [{"repo_full_name": "lc/repo", "synced": 2, "failed": 1, "skipped": 0}],
    }
    assert sweep.await_args.args == ("lc", "repo")


def test_stale_sync_runs_one_sweep_now(monkeypatch: pytest.MonkeyPatch) -> None:
    sweep = AsyncMock(return_value=SweepReport(synced=3))
    monkeypatch.setattr(pull_request_sync_routes, "run_sweep_once", sweep)

    response = _client(monkeypatch, login="alice").post(STALE_PATH)

    assert response.status_code == 200
    assert response.json()["synced"] == 3
    sweep.assert_awaited_once()
