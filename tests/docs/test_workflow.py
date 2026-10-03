"""Docs behavior contracts, with no model, sandbox, or GitHub network calls."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agent.docs import coordinator, github
from agent.docs.models import (
    JOBS,
    SETTINGS,
    DocsJob,
    DocsSettings,
    DocsSnapshot,
    Label,
    LinkedPR,
    PullRequest,
    eligible,
)
from agent.docs.publish import Finding, finish, publish
from agent.docs.routes import router
from agent.docs.runtime import current_docs_job
from agent.run_config import RunConfig
from agent.thread_ids import reviewer_thread_id
from tests.conftest import FakeStore


def snapshot() -> DocsSnapshot:
    config = DocsSettings(
        enabled=True,
        docs_repository="org/docs",
        source_repositories=["org/app"],
        revision="version-1",
    )
    pr = PullRequest.model_validate(
        {
            "number": 12,
            "state": "open",
            "draft": False,
            "title": "New feature",
            "body": "Feature description",
            "html_url": "https://github.com/org/app/pull/12",
            "head": {"sha": "a" * 40, "ref": "feature"},
            "base": {
                "sha": "b" * 40,
                "ref": "main",
                "repo": {"full_name": "org/app", "private": False},
            },
            "labels": [],
        }
    )
    return DocsSnapshot(
        source_repository="org/app", source=pr, settings=config, docs_base_sha="c" * 40
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/ORG/docs/pull/31",
        "https://github.com/org/docs/pull/31/files?x=1#diff",
        "(https://github.com/org/docs/pull/31).",
    ],
)
def test_ordinary_docs_urls(url: str) -> None:
    assert github.linked_numbers(url + " " + url, "org/docs") == [31]


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com.evil.test/org/docs/pull/31",
        "https://github.com/org/docs-evil/pull/31",
        "https://github.com/org/app/pull/31",
        "https://github.com@evil.test/org/docs/pull/31",
        "https://someone@github.com/org/docs/pull/31",
        "https://github.com/org/docs/pull/31oops",
        "https://[oops/org/docs/pull/31",
    ],
)
def test_spoofed_or_wrong_repo_links_are_ignored(url: str) -> None:
    assert github.linked_numbers(url, "org/docs") == []


@pytest.mark.parametrize("change", ["draft", "closed", "skip-docs", "disabled", "unselected"])
async def test_excluded_prs_never_dispatch_or_prepare_a_sandbox(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    evidence = snapshot()
    if change == "draft":
        evidence.source.draft = True
    if change == "closed":
        evidence.source.state = "closed"
    if change == "skip-docs":
        evidence.source.labels = [Label(name="skip-docs")]
    if change == "disabled":
        evidence.settings.enabled = False
    if change == "unselected":
        evidence.settings.source_repositories = []
    await SETTINGS.put("default", evidence.settings)
    monkeypatch.setattr(coordinator, "job_lock", unlocked)
    monkeypatch.setattr(coordinator, "repo_is_routable", AsyncMock(return_value=True))
    monkeypatch.setattr(github, "pull_request", AsyncMock(return_value=evidence.source))
    dispatch = AsyncMock()
    details = AsyncMock()
    monkeypatch.setattr(coordinator, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(github, "snapshot", details)
    assert await coordinator.coordinate("org/app", 12) == "skipped"
    dispatch.assert_not_awaited()
    details.assert_not_awaited()
    assert not eligible(evidence.settings, "org/app", evidence.source)


@asynccontextmanager
async def unlocked(_key: str):
    yield


async def seed(evidence: DocsSnapshot) -> DocsJob:
    await SETTINGS.put("default", evidence.settings)
    job = DocsJob(snapshot=evidence, thread_id="docs-thread", status="running", run_id="docs-run")
    await JOBS.put(evidence.key, job)
    return job


async def test_publishing_rechecks_skip_label_and_source_sha(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = snapshot()
    await seed(evidence)
    changed = evidence.source.model_copy(deep=True)
    changed.labels = [Label(name="skip-docs")]
    monkeypatch.setattr(github, "pull_request", AsyncMock(return_value=changed))
    with pytest.raises(ValueError, match="skip-docs"):
        await coordinator.publication_guard(evidence)
    changed.labels = []
    newer = evidence.model_copy(deep=True)
    newer.source.head.sha = "d" * 40
    monkeypatch.setattr(github, "pull_request", AsyncMock(return_value=newer.source))
    monkeypatch.setattr(github, "snapshot", AsyncMock(return_value=newer))
    with pytest.raises(ValueError, match="changed"):
        await coordinator.publication_guard(evidence)


async def test_linked_pr_requires_review_and_draft_docs_is_valid(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = snapshot()
    evidence.links = [
        LinkedPR(
            number=31,
            sha="e" * 40,
            base_sha="c" * 40,
            url="https://github.com/org/docs/pull/31",
            state="open",
            draft=True,
        )
    ]
    job = await seed(evidence)
    monkeypatch.setattr("agent.docs.publish.job_lock", unlocked)
    monkeypatch.setattr("agent.docs.publish.publication_guard", AsyncMock(return_value=job))
    monkeypatch.setattr("agent.docs.publish.complete_check", AsyncMock())
    posts = AsyncMock()
    writes = AsyncMock(return_value={"full_name": "org/docs", "private": False})
    monkeypatch.setattr("agent.docs.publish.comment_once", posts)
    monkeypatch.setattr(github, "request", writes)
    backend = MagicMock()
    with pytest.raises(ValueError, match="review-only"):
        await publish(evidence, backend, "/docs", "title", "body")
    backend.aexecute.assert_not_called()
    assert (
        await finish(
            evidence,
            "Fix the configuration example",
            [Finding(docs_pr_number=31, description="guide.mdx: document the new option")],
            True,
        )
        == "Docs review completed."
    )
    assert posts.await_args_list[0].args[:2] == ("org/docs", 31)
    assert posts.await_args_list[1].args[:2] == ("org/app", 12)
    assert all(call.kwargs.get("method", "GET") == "GET" for call in writes.await_args_list)
    assert (await JOBS.get(evidence.key)).status == "completed"


@pytest.mark.parametrize("code_review,docs", [(False, True), (True, True), (True, False)])
async def test_concurrent_webhook_and_coding_hook_create_one_run(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch, code_review: bool, docs: bool
) -> None:
    evidence = snapshot()
    await SETTINGS.put("default", evidence.settings)
    if not docs:
        evidence.source.labels = [Label(name="skip-docs")]
    monkeypatch.setattr(coordinator, "is_review_repo_enabled", AsyncMock(return_value=code_review))
    mutex = asyncio.Lock()

    @asynccontextmanager
    async def serialized(_key: str):
        async with mutex:
            yield

    client = MagicMock()
    client.runs.get = AsyncMock(return_value={"status": "running"})
    client.threads.update = AsyncMock()
    monkeypatch.setattr(coordinator, "get_thread_metadata", AsyncMock(return_value={}))
    monkeypatch.setattr(coordinator, "set_reviewer_thread_metadata", AsyncMock())
    monkeypatch.setattr("agent.webhooks.common.track_review_check_run", AsyncMock())
    monkeypatch.setattr(coordinator, "get_client", lambda: client)
    monkeypatch.setattr(coordinator, "job_lock", serialized)
    monkeypatch.setattr(coordinator, "repo_is_routable", AsyncMock(return_value=True))
    monkeypatch.setattr(coordinator, "workspace_for_repo", AsyncMock(return_value="default"))
    monkeypatch.setattr(coordinator, "create_thread", AsyncMock())
    monkeypatch.setattr(coordinator, "create_review_check_run", AsyncMock(return_value=99))
    monkeypatch.setattr(github, "token", AsyncMock(return_value="test-app-token"))
    monkeypatch.setattr(github, "pull_request", AsyncMock(return_value=evidence.source))
    monkeypatch.setattr(github, "snapshot", AsyncMock(return_value=evidence))
    dispatch = AsyncMock(return_value={"run_id": "one-run"})
    monkeypatch.setattr(coordinator, "dispatch_agent_run", dispatch)
    assert sorted(
        await asyncio.gather(
            coordinator.coordinate("org/app", 12), coordinator.coordinate("org/app", 12)
        )
    ) == ["dispatched", "unchanged"]
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    args, kwargs = dispatch.await_args
    assert args[0] == reviewer_thread_id("org", "app", 12)
    assert kwargs["assistant_id"] == "reviewer"
    assert args[2]["code_review_enabled"] is code_review
    assert args[2]["docs_enabled"] is docs
    job = await JOBS.get(evidence.key)
    assert job is not None and job.thread_id == args[0]


async def test_graph_rechecks_eligibility_before_model_or_sandbox(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = snapshot()
    await seed(evidence)
    evidence.source.draft = True
    monkeypatch.setattr(github, "pull_request", AsyncMock(return_value=evidence.source))
    with pytest.raises(ValueError, match="draft"):
        await current_docs_job(
            RunConfig(
                docs_job_key=evidence.key,
                docs_fingerprint=(await JOBS.get(evidence.key)).snapshot.fingerprint,
            )
        )


async def test_docs_settings_are_admin_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.dashboard.oauth import require_session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_session] = lambda: {"sub": "non-admin"}
    monkeypatch.setattr("agent.dashboard.deps.session_is_admin", lambda _session: False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/docs-settings")).status_code == 403
        assert (await client.put("/docs-settings", json={})).status_code == 403
        assert (
            await client.put("/enabled-docs-repos", json={"full_name": "org/app", "enabled": True})
        ).status_code == 403


async def test_created_docs_pr_is_reused_after_source_comment_failure(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.docs.publish as publisher
    from agent.docs.publish import Change

    evidence = snapshot()
    job = await seed(evidence)
    monkeypatch.setattr(publisher, "job_lock", unlocked)
    monkeypatch.setattr(publisher, "publication_guard", AsyncMock(return_value=job))
    monkeypatch.setattr(publisher, "complete_check", AsyncMock())
    collect = AsyncMock(return_value=[Change(path="guide.md", content="ZG9jcw==")])
    monkeypatch.setattr(publisher, "collect_changes", collect)
    created: list[dict[str, object]] = []

    async def request(
        repository: str,
        path: str,
        *,
        method: str = "GET",
        data: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> object:
        assert repository == "org/docs"
        if not path:
            return {"full_name": repository, "private": False}
        if path.startswith("pulls?"):
            return created
        if path.startswith("git/commits/"):
            return {"sha": "c" * 40, "tree": {"sha": "f" * 40}}
        if path in {"git/blobs", "git/trees", "git/commits", "git/refs"}:
            return {"sha": "f" * 40}
        if path == "pulls" and method == "POST":
            assert data is not None and data["draft"] is True
            result = {
                "html_url": "https://github.com/org/docs/pull/31",
                "number": 31,
                "state": "open",
            }
            created.append(result)
            return result
        raise AssertionError(path)

    monkeypatch.setattr(github, "request", request)
    monkeypatch.setattr(github, "token", AsyncMock(return_value="test-token"))

    @asynccontextmanager
    async def client(**_kwargs: object):
        yield MagicMock()

    monkeypatch.setattr(github, "github_client", client)
    response = MagicMock(status_code=404)
    monkeypatch.setattr(github, "github_request", AsyncMock(return_value=response))
    comment = AsyncMock(side_effect=[RuntimeError("source comment unavailable"), None])
    monkeypatch.setattr(publisher, "comment_once", comment)
    with pytest.raises(RuntimeError, match="comment unavailable"):
        await publish(
            evidence, MagicMock(), "/docs", "Document new behavior", "Updated documentation"
        )
    assert (await JOBS.get(evidence.key)).docs_pr_url == "https://github.com/org/docs/pull/31"
    assert (
        await publish(
            evidence, MagicMock(), "/docs", "Document new behavior", "Updated documentation"
        )
        == "https://github.com/org/docs/pull/31"
    )
    assert len(created) == 1
    collect.assert_awaited_once()


async def test_linked_docs_push_rechecks_each_source(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = snapshot()
    evidence.links = [
        LinkedPR(
            number=31,
            sha="e" * 40,
            base_sha="c" * 40,
            url="https://github.com/org/docs/pull/31",
            state="open",
            draft=True,
        )
    ]
    await seed(evidence)
    launch = AsyncMock(return_value="dispatched")
    monkeypatch.setattr(coordinator, "coordinate", launch)
    assert await coordinator.handle_event(
        {
            "repository": {"full_name": "org/docs"},
            "pull_request": {"number": 31},
            "action": "synchronize",
        },
        "pull_request",
    )
    launch.assert_awaited_once_with("org/app", 12)


async def test_public_repo_gate_does_not_prevent_skip_cancellation(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = snapshot()
    job = await seed(evidence)
    evidence.source.labels = [Label(name="skip-docs")]
    monkeypatch.setattr(coordinator, "job_lock", unlocked)
    monkeypatch.setattr(coordinator, "is_review_repo_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(coordinator, "repo_is_routable", AsyncMock(return_value=True))
    monkeypatch.setattr(github, "pull_request", AsyncMock(return_value=evidence.source))
    stop = AsyncMock()
    monkeypatch.setattr(coordinator, "cancel", stop)
    dispatch = AsyncMock()
    monkeypatch.setattr(coordinator, "dispatch_agent_run", dispatch)
    assert await coordinator.coordinate("org/app", 12, allow_dispatch=False) == "skipped"
    assert stop.await_args.args[0].thread_id == job.thread_id
    assert "skip-docs" in stop.await_args.args[1]
    dispatch.assert_not_awaited()


async def test_docs_repo_switch_preserves_independent_code_review_setting(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent.docs import routes
    from agent.review.enabled_repos import list_enabled_review_repos, set_review_repo_enabled

    config = snapshot().settings.model_copy(update={"source_repositories": []})
    await SETTINGS.put("default", config)
    await set_review_repo_enabled("org/app", True)
    monkeypatch.setattr(routes, "job_lock", unlocked)
    monkeypatch.setattr(github, "token", AsyncMock(return_value="test-app-token"))
    monkeypatch.setattr(github, "ensure_skip_label", AsyncMock())
    monkeypatch.setattr(github, "request", AsyncMock(return_value={"sha": "c" * 40}))
    assert await routes.put_docs_repository(
        routes.DocsRepoUpdate(full_name="ORG/app", enabled=True), {}
    ) == {"repos": ["org/app"]}
    assert await list_enabled_review_repos() == ["org/app"]
    assert await routes.put_docs_repository(
        routes.DocsRepoUpdate(full_name="org/app", enabled=False), {}
    ) == {"repos": []}
    assert await list_enabled_review_repos() == ["org/app"]
