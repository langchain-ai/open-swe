import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.threads import pr_fixes

CAMEL_CASE_CONTEXT: dict[str, Any] = {
    "title": "Broken build",
    "headRef": "feature/fix",
    "headSha": "abc123",
    "mergeable": False,
    "mergeState": "dirty",
    "ci": "failing",
    "failingChecks": ["lint", "unit", "build", "integration"],
    "pendingChecks": ["e2e"],
    "statusAvailable": True,
    "updatedAt": "2026-09-12T12:00:00Z",
    "reviewDecision": "changes_requested",
}

OPEN = pr_fixes.OpenThreadIntent(intent="open", title="Fix broken build")
FIX = pr_fixes.FixIntent(intent="fix")
ADDRESS_COMMENTS = pr_fixes.AddressCommentsIntent(intent="address-comments")

PR_URL = "https://github.com/acme/app/pull/12"


@asynccontextmanager
async def unlocked(*args):
    yield


class FakeRegistry:
    """Stands in for the ``pull_request`` record and its thread links."""

    thread_ids: list[str] = []
    linked: list[tuple[str, str]] = []
    unavailable: bool = False
    discovered: list[str] = []

    def __init__(self, owner: str, repo: str, number: int) -> None:
        self.owner = owner
        self.repo = repo
        self.number = number

    @classmethod
    async def load(cls, owner: str, repo: str, number: int) -> FakeRegistry:
        if cls.unavailable:
            raise RuntimeError("postgres unavailable")
        return cls(owner, repo, number)

    async def linked_threads(self) -> list[str]:
        return list(type(self).thread_ids)

    async def discover_threads(self) -> list[str]:
        return list(type(self).discovered)

    async def link_thread(self, thread_id: str, *, source: str = "") -> FakeRegistry:
        type(self).linked.append((thread_id, source))
        return self


@pytest.fixture
def setup(monkeypatch):
    threads: dict[str, dict[str, Any]] = {
        "existing": {"thread_id": "existing", "metadata": {"source": "dashboard"}}
    }
    FakeRegistry.thread_ids = ["existing"]
    FakeRegistry.linked = []
    FakeRegistry.discovered = []
    FakeRegistry.unavailable = False

    async def get(thread_id: str) -> dict[str, Any]:
        return threads[thread_id]

    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(side_effect=get), update=AsyncMock())
    )
    monkeypatch.setattr(pr_fixes, "langgraph_client", lambda: client)
    monkeypatch.setattr(pr_fixes, "agent_thread_pr_state_lock", unlocked)
    monkeypatch.setattr(pr_fixes, "PullRequest", FakeRegistry)
    for name in (
        "require_repo_access_for_user",
        "_ensure_dashboard_github_token",
        "dispatch_agent_run",
    ):
        monkeypatch.setattr(pr_fixes, name, AsyncMock())
    monkeypatch.setattr(pr_fixes, "_build_dashboard_configurable", AsyncMock(return_value={}))
    monkeypatch.setattr(
        pr_fixes,
        "_create_dashboard_thread_record",
        AsyncMock(return_value={"thread_id": "new", "metadata": {}}),
    )
    threads["new"] = {"thread_id": "new", "metadata": {"source": "dashboard"}}
    return SimpleNamespace(client=client, threads=threads)


def test_fix_context_accepts_the_camel_case_payload_the_browser_posts():
    context = pr_fixes.PullRequestFixContext.model_validate(CAMEL_CASE_CONTEXT)

    assert context.head_ref == "feature/fix"
    assert context.head_sha == "abc123"
    assert context.merge_state == "dirty"
    assert context.failing_checks == ["lint", "unit", "build", "integration"]
    assert context.pending_checks == ["e2e"]
    assert context.status_available is True
    assert context.updated_at == "2026-09-12T12:00:00Z"
    assert context.review_decision == "changes_requested"


def test_thread_run_json_keys_match_the_dashboard_client():
    assert pr_fixes.PullRequestThreadRun(thread_id="t1").model_dump(mode="json") == {
        "thread_id": "t1",
        "already_running": False,
    }
    assert pr_fixes.PullRequestThreadStatus(running=False).model_dump(mode="json") == {
        "running": False
    }


@pytest.mark.parametrize(
    "intent,template,title,dispatches",
    [
        (OPEN, "runs/pull-request-thread", "Fix broken build", False),
        (FIX, "runs/pull-request-fix", "Fix acme/app#12", True),
        (
            ADDRESS_COMMENTS,
            "runs/pull-request-comments",
            "Address comments on acme/app#12",
            True,
        ),
    ],
)
async def test_each_intent_names_its_own_prompt_and_thread_title(
    setup, intent, template, title, dispatches
):
    FakeRegistry.thread_ids = []
    prompt = pr_fixes.prompt(template, url=PR_URL, comment_url="")

    assert await pr_fixes.start_pull_request_thread(
        "acme", "app", 12, "alice", intent=intent
    ) == pr_fixes.PullRequestThreadRun(thread_id="new")
    created = pr_fixes._create_dashboard_thread_record.await_args.kwargs
    assert (created["prompt"], created["title"]) == (prompt, title)
    if dispatches:
        args = pr_fixes.dispatch_agent_run.await_args
        assert args.args[:2] == ("new", prompt)
        assert args.kwargs["multitask_strategy"] == "enqueue"
    else:
        pr_fixes.dispatch_agent_run.assert_not_awaited()


async def test_every_intent_locks_on_the_same_key_so_only_one_thread_is_created(setup, monkeypatch):
    keys: list[str] = []

    @asynccontextmanager
    async def recording_lock(_client, key: str):
        keys.append(key)
        yield

    monkeypatch.setattr(pr_fixes, "agent_thread_pr_state_lock", recording_lock)

    for intent in (OPEN, FIX, ADDRESS_COMMENTS):
        await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=intent)

    assert [key for key in keys if key.startswith("pr-thread:")] == [
        f"pr-thread:alice:{PR_URL}"
    ] * 3


async def test_reuses_an_existing_thread_rather_than_creating_another(setup):
    assert await pr_fixes.start_pull_request_thread(
        "acme", "app", 12, "alice", intent=FIX
    ) == pr_fixes.PullRequestThreadRun(thread_id="existing")
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()
    assert pr_fixes.dispatch_agent_run.await_args.args[0] == "existing"


async def test_creates_when_only_review_or_private_threads_are_linked(setup):
    setup.threads["review"] = {"thread_id": "review", "metadata": {"kind": "reviewer"}}
    setup.threads["private"] = {
        "thread_id": "private",
        "metadata": {"source": "dashboard", "visibility": "private", "owner_login": "bob"},
    }
    FakeRegistry.thread_ids = ["review", "private"]

    result = await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=FIX)

    assert result == pr_fixes.PullRequestThreadRun(thread_id="new")
    assert pr_fixes._create_dashboard_thread_record.await_args.kwargs["repo_config"] == {
        "owner": "acme",
        "name": "app",
    }
    setup.client.threads.update.assert_any_await(
        thread_id="new",
        metadata={"pr_url": PR_URL, "pr_number": 12, "source_context": {"pr_number": 12}},
    )
    assert FakeRegistry.linked == [("new", "dashboard_pr_fix")]


async def test_linked_thread_the_caller_cannot_post_to_is_never_reused(setup, monkeypatch):
    monkeypatch.setenv("CONFIGURED_ADMINS", "")
    setup.threads["admin"] = {
        "thread_id": "admin",
        "metadata": {"source": "dashboard", "admin_thread": True},
    }
    setup.threads["private"] = {
        "thread_id": "private",
        "metadata": {"source": "dashboard", "visibility": "private", "owner_login": "bob"},
    }
    FakeRegistry.thread_ids = ["admin", "private"]

    assert await pr_fixes._find_pr_threads("acme", "app", 12, "alice", None) == []
    assert await pr_fixes.pull_request_thread_running(
        "acme", "app", 12, "alice"
    ) == pr_fixes.PullRequestThreadStatus(running=False)
    assert await pr_fixes.start_pull_request_thread(
        "acme", "app", 12, "alice", intent=FIX
    ) == pr_fixes.PullRequestThreadRun(thread_id="new")


async def test_unreachable_registry_falls_back_to_the_legacy_metadata_scan(setup):
    FakeRegistry.unavailable = True
    FakeRegistry.discovered = ["existing"]

    result = await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=FIX)

    assert result == pr_fixes.PullRequestThreadRun(thread_id="existing")
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()


async def test_denied_repo_never_reads_or_starts_threads(setup):
    pr_fixes.require_repo_access_for_user.side_effect = HTTPException(403, "denied")
    with pytest.raises(HTTPException):
        await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=FIX)
    setup.client.threads.get.assert_not_awaited()
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()
    pr_fixes.dispatch_agent_run.assert_not_awaited()


async def test_an_invalid_pull_request_number_is_rejected_before_any_lookup(setup):
    with pytest.raises(HTTPException) as raised:
        await pr_fixes.start_pull_request_thread("acme", "app", 0, "alice", intent=FIX)
    assert raised.value.status_code == 422
    pr_fixes.require_repo_access_for_user.assert_not_awaited()
    pr_fixes.dispatch_agent_run.assert_not_awaited()


async def test_fix_message_includes_full_displayed_failure_context(setup):
    context = pr_fixes.PullRequestFixContext.model_validate(CAMEL_CASE_CONTEXT)
    intent = pr_fixes.FixIntent(intent="fix", context=context)

    await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=intent)

    prompt = pr_fixes.dispatch_agent_run.await_args.args[1]
    assert prompt.startswith(pr_fixes.prompt("runs/pull-request-fix", url=PR_URL))
    assert json.loads(prompt[prompt.index("{\n") :]) == context.model_dump()


async def test_single_comment_run_names_the_comment_and_carries_instructions(setup):
    comment_url = f"{PR_URL}#discussion_r42"
    intent = pr_fixes.AddressCommentIntent(
        intent="address-comment", comment_url=comment_url, instructions="  keep the old name  "
    )

    await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=intent)

    prompt = pr_fixes.dispatch_agent_run.await_args.args[1]
    assert prompt.startswith(
        pr_fixes.prompt("runs/pull-request-comments", url=PR_URL, comment_url=comment_url)
    )
    assert prompt.endswith("\nkeep the old name")


@pytest.mark.parametrize(
    "comment_url",
    [
        "https://github.com/acme/app/pull/13#discussion_r42",
        "https://github.com/acme/other/pull/12#discussion_r42",
        "https://evil.example/acme/app/pull/12#discussion_r42",
    ],
)
async def test_single_comment_run_rejects_a_comment_from_another_pull_request(setup, comment_url):
    intent = pr_fixes.AddressCommentIntent(intent="address-comment", comment_url=comment_url)

    with pytest.raises(HTTPException) as exc:
        await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=intent)

    assert exc.value.status_code == 422
    pr_fixes.dispatch_agent_run.assert_not_awaited()


async def test_opening_a_thread_neither_dispatches_a_run_nor_mutates_it(setup):
    result = await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=OPEN)

    assert result == pr_fixes.PullRequestThreadRun(thread_id="existing")
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()
    pr_fixes.dispatch_agent_run.assert_not_awaited()
    setup.client.threads.update.assert_not_awaited()


async def test_new_thread_supplies_pr_context_to_first_user_run(setup, monkeypatch):
    from agent.threads import runs

    FakeRegistry.thread_ids = []
    await pr_fixes.start_pull_request_thread("acme", "app", 12, "alice", intent=OPEN)
    metadata = setup.client.threads.update.await_args.kwargs["metadata"]
    monkeypatch.setattr(runs, "resolve_run_email", AsyncMock(return_value=None))
    configurable = await runs._build_dashboard_configurable(
        "new",
        "alice",
        {**metadata, "repo_owner": "acme", "repo_name": "app", "source": "dashboard"},
        profile={},
    )
    assert configurable["pr_number"] == 12
    assert configurable["repo"] == {"owner": "acme", "name": "app"}
    pr_fixes.dispatch_agent_run.assert_not_awaited()


async def test_a_busy_thread_is_reported_instead_of_being_sent_another_run(setup):
    setup.threads["slack-thread"] = {
        "thread_id": "slack-thread",
        "status": "busy",
        "metadata": {"source": "slack", "graph_id": "agent"},
    }
    setup.threads["duplicate"] = {
        "thread_id": "duplicate",
        "status": "idle",
        "updated_at": "2099-01-01",
        "metadata": {"source": "dashboard"},
    }
    FakeRegistry.thread_ids = ["duplicate", "slack-thread"]

    assert await pr_fixes.pull_request_thread_running(
        "acme", "app", 12, "alice"
    ) == pr_fixes.PullRequestThreadStatus(running=True)
    assert await pr_fixes.start_pull_request_thread(
        "acme", "app", 12, "alice", intent=FIX
    ) == pr_fixes.PullRequestThreadRun(thread_id="slack-thread", already_running=True)
    assert await pr_fixes.start_pull_request_thread(
        "acme", "app", 12, "alice", intent=OPEN
    ) == pr_fixes.PullRequestThreadRun(thread_id="slack-thread", already_running=False)
    pr_fixes.dispatch_agent_run.assert_not_awaited()
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()
    setup.client.threads.update.assert_not_awaited()


async def test_no_associated_thread_status_does_not_create_one(setup):
    FakeRegistry.thread_ids = []
    assert await pr_fixes.pull_request_thread_running(
        "acme", "app", 12, "alice"
    ) == pr_fixes.PullRequestThreadStatus(running=False)
    pr_fixes._create_dashboard_thread_record.assert_not_awaited()
