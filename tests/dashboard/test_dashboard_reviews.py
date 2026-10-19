from time import monotonic
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest

from openswe.github import app as github_app
from openswe.github import http as github_http
from openswe.github import routes as github_routes
from openswe.github.http import GitHubError
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import PullRequestClient
from openswe.review import findings as findings_store
from openswe.review import reviews as review_api
from openswe.review.findings import REVIEWER_THREAD_KIND, new_finding
from openswe.thread_ids import reviewer_thread_id
from openswe.ui_invalidations import Topic
from openswe.ui_invalidations.outbox import invalidated_since
from openswe.webhooks import common as webhook_common
from tests.conftest import post_signed_github_webhook

pytestmark = pytest.mark.usefixtures("findings_from_metadata")


@pytest.fixture(autouse=True)
def github_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_app, "get_github_app_installation_id_for_repo", AsyncMock(return_value=123)
    )


def _thread(owner: str, name: str, number: int, author: str) -> dict[str, Any]:
    return {
        "thread_id": f"{owner}/{name}/{number}",
        "status": "idle",
        "updated_at": "2026-06-12T00:00:00Z",
        "metadata": {
            "kind": REVIEWER_THREAD_KIND,
            "pr": {"owner": owner, "name": name, "number": number, "author": author},
        },
    }


def _fake_client(pages: list[list[dict[str, Any]]]) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {"calls": []}
    queue = list(pages)

    async def search(**kwargs: Any) -> list[dict[str, Any]]:
        captured["calls"].append(kwargs)
        return queue.pop(0) if queue else []

    return SimpleNamespace(threads=SimpleNamespace(search=search)), captured


@pytest.mark.asyncio
async def test_list_reviews_applies_accessibility_and_has_more(monkeypatch) -> None:
    page = [
        _thread("acme", "a", 1, "octocat"),
        _thread("acme", "b", 2, "octocat"),
        _thread("acme", "a", 3, "octocat"),
    ]
    client, _ = _fake_client([page])
    monkeypatch.setattr(review_api, "langgraph_client", lambda: client)

    async def is_accessible(summary: dict[str, Any]) -> bool:
        return summary["full_name"] == "acme/a"

    reviews, has_more = await review_api.list_reviews(1, offset=0, is_accessible=is_accessible)

    assert [r["number"] for r in reviews] == [1]
    # two accessible records exist (1 and 3); page of 1 leaves more.
    assert has_more is True


@pytest.mark.asyncio
async def test_get_review_propagates_a_pull_request_missing_on_github(monkeypatch) -> None:
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    missing = httpx2.Response(
        404, json={"message": "Not Found"}, request=httpx2.Request("GET", "https://api.github.com")
    )
    monkeypatch.setattr(github_http, "github_request", AsyncMock(return_value=missing))

    with pytest.raises(GitHubError) as excinfo:
        await review_api.get_review("acme", "app", 7)

    assert excinfo.value.response.status_code == 404


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize("github_failure", [False, True])
async def test_review_page_reconciles_stored_flags_and_invalidates_after_missed_webhook(
    monkeypatch: pytest.MonkeyPatch, github_failure: bool
) -> None:
    thread = _thread("acme", "app", 7, "octocat")
    thread["thread_id"] = reviewer_thread_id("acme", "app", 7)
    finding = new_finding(
        severity="medium",
        category="bug",
        file="a.py",
        start_line=19,
        end_line=19,
        description="Combining marks are excluded",
        sha="old",
    )
    finding["github_review_thread_ids"] = ["THREAD_1"]
    finding["surface_state"] = "surfaced"
    metadata_client = AsyncMock()
    metadata_client.threads.get.return_value = thread
    monkeypatch.setattr(findings_store, "get_client", lambda: metadata_client)
    monkeypatch.setattr(review_api, "_reviewer_thread_for", AsyncMock(return_value=thread))
    monkeypatch.setattr(review_api, "_scout_target", AsyncMock(return_value=None))
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(return_value={"number": 7, "head": {"sha": "new"}, "base": {}}),
    )
    monkeypatch.setattr(github_http.RepoClient, "check_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        PullRequestClient,
        "review_thread_states",
        AsyncMock(side_effect=ValueError("Incomplete GitHub response"))
        if github_failure
        else AsyncMock(return_value={"THREAD_1": True}),
    )
    await findings_store.append_finding(thread["thread_id"], finding)
    started = monotonic()
    payload = await review_api.get_review("acme", "app", 7)
    persisted = await findings_store.list_findings(thread["thread_id"])

    expected_status = "open" if github_failure else "resolved"
    assert payload["findings"][0]["status"] == expected_status
    assert persisted[0]["status"] == expected_status
    assert payload["counts"]["flags"] == int(github_failure)
    assert payload["counts"]["open"] == int(github_failure)
    topic = f"{Topic.PULL_REQUESTS}/{PullRequestKey.of('acme', 'app', 7)}"
    assert (topic in await invalidated_since({topic: monotonic() - started})) is not github_failure

    if not github_failure:
        monkeypatch.setattr(github_routes, "repo_is_routable", AsyncMock(return_value=True))
        monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", "thread-state-secret")
        monkeypatch.setattr(
            webhook_common, "get_thread_metadata_safe", AsyncMock(return_value=thread["metadata"])
        )
        monkeypatch.setattr(
            PullRequestClient, "review_thread_states", AsyncMock(return_value={"THREAD_1": False})
        )
        for action in ("unresolved", "resolved"):
            response = await post_signed_github_webhook(
                "pull_request_review_thread",
                {
                    "action": action,
                    "repository": {"owner": {"login": "acme"}, "name": "app"},
                    "pull_request": {"number": 7},
                },
                secret="thread-state-secret",
            )
            assert response.json()["status"] == "accepted"
            persisted = await findings_store.list_findings(thread["thread_id"])
            assert persisted[0]["status"] == "open"
            assert persisted[0]["github_resolved_thread_ids"] == []
        reopened = await review_api.get_review("acme", "app", 7)
        assert reopened["counts"]["flags"] == 1
