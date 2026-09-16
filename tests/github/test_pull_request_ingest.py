"""Route-level coverage for storing pull request state from GitHub webhooks."""

import hashlib
import hmac
import json
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient

from agent.api.app import app
from agent.github import pull_request_events as events
from agent.github import webhook as github
from agent.github.pull_requests import PullRequest, PullRequestCheck
from agent.webhooks import common

_SECRET = "pull-request-ingest-secret"
HEAD_SHA = "a" * 40
REPOSITORY = {"owner": {"login": "acme"}, "name": "repo", "full_name": "acme/repo"}


def _post(event_type: str, payload: dict[str, Any]):
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return TestClient(app).post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": event_type,
            "X-GitHub-Delivery": "delivery-1",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )


def _pull_request_payload(action: str) -> dict[str, Any]:
    return {
        "action": action,
        "pull_request": {
            "number": 7,
            "title": "Add widget",
            "state": "open",
            "draft": False,
            "merged": False,
            "updated_at": "2026-02-01T00:00:00Z",
            "user": {"login": "ada", "id": 42},
            "head": {"ref": "feature", "sha": HEAD_SHA},
            "base": {"ref": "main", "sha": "b" * 40, "repo": {"private": False}},
        },
        "repository": {**REPOSITORY, "private": False},
    }


class _StoredPullRequest:
    """Stands in for a stored row; records what a webhook writes onto it."""

    def __init__(self, number: int = 7, *, synced: bool = True) -> None:
        self.number = number
        self.last_synced_at = "2026-02-01T00:00:00Z" if synced else None
        self.checks: list[PullRequestCheck] = []

    async def upsert_check(self, check: PullRequestCheck) -> Self:
        self.checks.append(check)
        return self

    async def linked_threads(self) -> list[str]:
        return []


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "GITHUB_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(common, "is_repo_allowed", lambda _repo: True)


@pytest.fixture
def saves(monkeypatch: pytest.MonkeyPatch) -> list[PullRequest]:
    recorded: list[PullRequest] = []

    async def save(
        self: PullRequest, *, repository_private: bool | None = None, synced: bool = False
    ) -> _StoredPullRequest:
        recorded.append(self)
        return _StoredPullRequest()

    monkeypatch.setattr(PullRequest, "save", save)
    return recorded


@pytest.mark.usefixtures("signed")
def test_synchronize_stores_the_pull_request_exactly_once(
    saves: list[PullRequest], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def usage(_payload: dict[str, Any]) -> None:
        return None

    monkeypatch.setattr(common, "update_agent_pr_usage_from_webhook", usage)

    response = _post("pull_request", _pull_request_payload("synchronize"))

    assert response.status_code == 200
    assert len(saves) == 1
    assert (saves[0].number, saves[0].head_sha, saves[0].state) == (7, HEAD_SHA, "open")


@pytest.mark.usefixtures("signed")
def test_action_without_a_handler_still_stores_the_pull_request(
    saves: list[PullRequest],
) -> None:
    response = _post("pull_request", _pull_request_payload("labeled"))

    assert response.status_code == 200
    assert [row.number for row in saves] == [7]


@pytest.fixture
def ci_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def process(payload: dict[str, Any], kind: str, delivery_id: str | None) -> None:
        return None

    monkeypatch.setattr(github, "process_github_ci_event", process)


@pytest.mark.usefixtures("signed", "ci_routed")
def test_check_run_upserts_a_check_on_the_pull_request_it_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = _StoredPullRequest()

    async def get(cls: type[PullRequest], owner: str, repo: str, number: int) -> _StoredPullRequest:
        assert (owner, repo, number) == ("acme", "repo", 7)
        return stored

    monkeypatch.setattr(PullRequest, "get", classmethod(get))

    response = _post(
        "check_run",
        {
            "action": "completed",
            "check_run": {
                "id": 991,
                "name": "lint",
                "status": "completed",
                "conclusion": "failure",
                "details_url": None,
                "html_url": "https://ci.example/lint",
                "head_sha": HEAD_SHA,
                "completed_at": "2026-02-01T00:05:00Z",
                "pull_requests": [{"number": 7}],
            },
            "repository": {**REPOSITORY, "private": False},
        },
    )

    assert response.status_code == 200
    assert len(stored.checks) == 1
    check = stored.checks[0]
    assert (check.kind, check.external_id, check.head_sha) == ("check_run", "991", HEAD_SHA)
    assert (check.conclusion, check.details_url) == ("failure", "https://ci.example/lint")


@pytest.mark.usefixtures("signed", "ci_routed")
def test_status_event_resolves_the_pull_request_by_head_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = _StoredPullRequest(number=12)

    async def for_head_sha(
        cls: type[PullRequest], owner: str, repo: str, sha: str
    ) -> list[_StoredPullRequest]:
        assert (owner, repo, sha) == ("acme", "repo", HEAD_SHA)
        return [stored]

    async def get(cls: type[PullRequest], owner: str, repo: str, number: int) -> _StoredPullRequest:
        assert number == 12
        return stored

    monkeypatch.setattr(PullRequest, "for_head_sha", classmethod(for_head_sha))
    monkeypatch.setattr(PullRequest, "get", classmethod(get))

    response = _post(
        "status",
        {
            "sha": HEAD_SHA,
            "context": "ci/circleci",
            "state": "success",
            "target_url": "https://ci.example/build",
            "updated_at": "2026-02-01T00:06:00Z",
            "branches": [{"name": "feature"}],
            "repository": {**REPOSITORY, "private": False},
        },
    )

    assert response.status_code == 200
    assert len(stored.checks) == 1
    check = stored.checks[0]
    assert (check.kind, check.external_id, check.conclusion) == ("status", "ci/circleci", "success")


@pytest.mark.usefixtures("signed", "ci_routed")
def test_check_run_for_an_unknown_pull_request_syncs_it_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = _StoredPullRequest()
    synced: list[tuple[str, str, int, str]] = []

    async def get(cls: type[PullRequest], owner: str, repo: str, number: int) -> None:
        return None

    async def token(*, repositories: list[str]) -> str:
        assert repositories == ["repo"]
        return "app-token"

    async def sync(owner: str, repo: str, number: int, *, token: str) -> _StoredPullRequest:
        synced.append((owner, repo, number, token))
        return stored

    monkeypatch.setattr(PullRequest, "get", classmethod(get))
    monkeypatch.setattr(events, "get_github_app_installation_token", token)
    monkeypatch.setattr(events, "sync_pull_request", sync)

    response = _post(
        "check_run",
        {
            "action": "created",
            "check_run": {
                "id": 7,
                "name": "build",
                "status": "in_progress",
                "head_sha": HEAD_SHA,
                "pull_requests": [{"number": 7}],
            },
            "repository": {**REPOSITORY, "private": False},
        },
    )

    assert response.status_code == 200
    assert synced == [("acme", "repo", 7, "app-token")]
    assert [check.external_id for check in stored.checks] == ["7"]


@pytest.mark.usefixtures("signed")
def test_review_comment_refreshes_the_review_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed: list[tuple[str, str, int, str]] = []

    async def get(cls: type[PullRequest], owner: str, repo: str, number: int) -> _StoredPullRequest:
        return _StoredPullRequest(number=number)

    async def token(*, repositories: list[str]) -> str:
        return "app-token"

    async def refresh(owner: str, repo: str, number: int, *, token: str) -> None:
        refreshed.append((owner, repo, number, token))

    monkeypatch.setattr(PullRequest, "get", classmethod(get))
    monkeypatch.setattr(events, "get_github_app_installation_token", token)
    monkeypatch.setattr(events, "sync_review_threads", refresh)

    response = _post(
        "pull_request_review_comment",
        {
            "action": "created",
            "comment": {"body": "looks fine"},
            "pull_request": {"number": 7},
            "repository": {**REPOSITORY, "private": False},
        },
    )

    assert response.status_code == 200
    assert refreshed == [("acme", "repo", 7, "app-token")]
