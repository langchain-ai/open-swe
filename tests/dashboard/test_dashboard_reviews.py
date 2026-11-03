from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from openswe.api.github_errors import add_github_error_handlers
from openswe.dashboard.oauth import require_session
from openswe.database import postgres
from openswe.github import app as github_app
from openswe.github import http as github_http
from openswe.github import routes as github_routes
from openswe.github.http import GitHubError
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.pull_request_status import PullRequestClient
from openswe.review import findings as findings_store
from openswe.review import reviews as review_api
from openswe.review import routes as review_routes
from openswe.review.findings import REVIEWER_THREAD_KIND, new_finding
from openswe.thread_ids import reviewer_thread_id
from openswe.ui_invalidations import Topic
from openswe.webhooks import common as webhook_common
from tests.conftest import post_signed_github_webhook

pytestmark = pytest.mark.usefixtures("findings_from_metadata")


@pytest.mark.parametrize(
    "denied, github_status, expected_status",
    [(False, 200, 204), (True, 200, 403), (False, 422, 422), (False, 503, 502)],
)
async def test_description_update_uses_viewer_permissions(
    monkeypatch, denied: bool, github_status: int, expected_status: int
) -> None:
    app = FastAPI()
    add_github_error_handlers(app)
    app.include_router(review_routes.router)
    app.dependency_overrides[require_session] = lambda: {"sub": "alice"}
    access = AsyncMock(side_effect=HTTPException(403, "No repository access") if denied else None)
    monkeypatch.setattr(review_routes, "require_repo_access_for_user", access)
    request = AsyncMock(
        return_value=httpx2.Response(
            github_status,
            json={"body": "Updated description", "message": "GitHub rejected this description"},
            request=httpx2.Request("PATCH", "https://api.github.com"),
        )
    )
    monkeypatch.setattr(github_http, "github_request", request)
    github = github_http.GitHubClient(MagicMock())
    viewer = MagicMock()
    viewer.return_value.__aenter__ = AsyncMock(return_value=github)
    viewer.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(review_routes.GitHubClient, "as_user", viewer)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.patch(
            "/reviews/acme/app/7/description", json={"body": "Updated description"}
        )
    assert response.status_code == expected_status
    if github_status >= 400:
        assert response.json() == {"detail": "GitHub rejected this description"}
    if denied:
        viewer.assert_not_called()
        request.assert_not_awaited()
    else:
        viewer.assert_called_once_with("alice")
        assert request.await_args.args[1:] == (
            "PATCH",
            "https://api.github.com/repos/acme/app/pulls/7",
        )
        assert request.await_args.kwargs["json"] == {"body": "Updated description"}


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


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [False, True])
async def test_review_trace_and_metadata_findings_survive_failed_backfill(
    monkeypatch: pytest.MonkeyPatch, linked: bool
) -> None:
    trace_url = "https://smith.langchain.com/trace/reviewer"
    monkeypatch.setattr(
        github_app, "get_github_app_installation_token", AsyncMock(return_value="t")
    )
    monkeypatch.setattr(
        github_http,
        "github_request",
        AsyncMock(
            return_value=httpx2.Response(
                200,
                json={"title": "Example", "state": "open"},
                request=httpx2.Request("GET", "https://api.github.com"),
            )
        ),
    )
    thread = _thread("acme", "app", 7, "alice")
    finding = new_finding(
        severity="medium",
        category="bug",
        file="a.py",
        start_line=1,
        end_line=1,
        description="Legacy finding",
        sha="old",
    )
    if linked:
        finding["github_review_thread_ids"] = ["THREAD_1"]
        finding["surface_state"] = "surfaced"
    thread["metadata"]["findings"] = [finding]
    monkeypatch.setattr(review_api, "findings_by_thread", findings_store.findings_by_thread)
    monkeypatch.setattr(findings_store, "_stored_findings", AsyncMock(return_value={}))
    monkeypatch.setattr(
        findings_store,
        "_ensure_finding_state",
        AsyncMock(side_effect=RuntimeError("Backfill failed")),
    )
    monkeypatch.setattr(
        PullRequestClient, "review_thread_states", AsyncMock(return_value={"THREAD_1": True})
    )
    monkeypatch.setattr(review_api, "_reviewer_thread_for", AsyncMock(return_value=thread))
    monkeypatch.setattr(review_api, "_scout_target", AsyncMock(return_value=None))
    trace = AsyncMock(return_value=trace_url)
    monkeypatch.setattr(review_api, "get_langsmith_trace_url", trace)

    detail = await review_api.get_review("acme", "app", 7)

    assert detail["trace_url"] == trace_url
    assert detail["findings"][0]["id"] == finding["id"]
    assert detail["findings"][0]["status"] == "open"
    assert detail["counts"]["flags"] == 1
    trace.assert_awaited_once_with("acme/app/7")


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize("github_failure", [False, True])
async def test_review_page_reconciles_stored_flags_and_invalidates_after_missed_webhook(
    monkeypatch: pytest.MonkeyPatch, github_failure: bool
) -> None:
    monkeypatch.setattr(review_api, "findings_by_thread", findings_store.findings_by_thread)
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
    async with postgres.transaction() as conn:
        await conn.execute(text("DELETE FROM ui_invalidation"))
    payload = await review_api.get_review("acme", "app", 7)
    persisted = await findings_store.list_findings(thread["thread_id"])

    expected_status = "open" if github_failure else "resolved"
    assert payload["findings"][0]["status"] == expected_status
    assert persisted[0]["status"] == expected_status
    assert payload["counts"]["flags"] == int(github_failure)
    assert payload["counts"]["open"] == int(github_failure)
    topic = f"{Topic.PULL_REQUESTS}/{PullRequestKey.of('acme', 'app', 7)}"
    async with postgres.connection() as conn:
        topics = set((await conn.execute(text("SELECT topic FROM ui_invalidation"))).scalars())
    assert (topic in topics) is not github_failure

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
