import asyncio
import hashlib
import hmac
import importlib
import uuid
from typing import cast
from unittest.mock import AsyncMock

import httpx2
import pytest
from langgraph_sdk.errors import NotFoundError
from runtime_api.github import GitHubAutomationRun

from openswe.github import channel as github_channel
from openswe.github import webhook as github_webhooks
from openswe.github.pull_requests import AGENT_OPENED_LINK_SOURCE, PullRequest, ThreadLink
from openswe.review.reviews import trigger_re_review
from openswe.schedules import store as schedules
from openswe.slack.client import GitHubPrRef
from openswe.slack.payloads import SlackChannelContext
from openswe.users import User
from openswe.webhooks import common as webhook_common
from tests.conftest import register_github_logins
from tests.github_channel import GitHubDelivered, deliver_github_event

pytestmark = pytest.mark.usefixtures("no_local_threads")

request_pr_review_module = importlib.import_module("openswe.slack.tools.request_pr_review")

_TEST_SLACK_SECRET = "test-slack-secret"


@pytest.fixture(autouse=True)
def _no_incident_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Slack webhook offers each event to Incidents first; none of these channels is one."""
    monkeypatch.setattr(
        "openswe.incidents.channels.handle_slack_event", AsyncMock(return_value=None)
    )


@pytest.fixture(autouse=True)
def _slack_routing_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(*args: object, **kwargs: object) -> str:
        return "mapped-slack-thread"

    async def lookup(*args: object, **kwargs: object) -> None:
        return None

    async def channel_context(*args: object, **kwargs: object) -> SlackChannelContext:
        return SlackChannelContext(is_ext_shared=False, is_pending_ext_shared=False)

    async def claim(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(webhook_common, "resolve_slack_thread_id", resolve)
    monkeypatch.setattr(webhook_common, "lookup_slack_thread_id", lookup)
    monkeypatch.setattr(webhook_common, "resolve_slack_channel_context", channel_context)
    monkeypatch.setattr(webhook_common, "claim_slack_event", claim)


async def _deliver(
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
    payload: dict[str, object],
    *,
    delivery_id: str | None = None,
) -> GitHubDelivered:
    return await deliver_github_event(monkeypatch, event_type, payload, delivery_id=delivery_id)


def _sign_slack_body(body: bytes, timestamp: str = "1700000000") -> str:
    base_string = f"v0:{timestamp}:{body.decode()}"
    sig = hmac.new(_TEST_SLACK_SECRET.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    return f"v0={sig}"


@pytest.mark.parametrize("wakeup_fails", [False, True])
async def test_github_webhook_skips_automatic_review_when_disabled(
    monkeypatch, registry_db, wakeup_fails: bool
) -> None:
    called = False

    async def fake_auto_review_enabled(_repo_config: dict[str, str]) -> bool:
        return False

    async def fake_process_github_pr_ready(_payload: dict[str, object]) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(webhook_common, "is_repo_auto_review_enabled", fake_auto_review_enabled)
    monkeypatch.setattr(github_webhooks, "process_github_pr_ready", fake_process_github_pr_ready)
    if wakeup_fails:
        monkeypatch.setattr(
            github_channel,
            "wake_native_queue",
            AsyncMock(side_effect=RuntimeError("DB unavailable")),
        )

    delivered = await _deliver(
        monkeypatch,
        "pull_request",
        {
            "action": "opened",
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "pull_request": {"number": 1244},
        },
    )

    assert delivered.facts.auto_review is False
    assert "review_pull_request" not in delivered.work
    assert called is False


async def test_github_webhook_plans_issue_automation_for_external_author(
    monkeypatch, registry_db
) -> None:
    called: dict[str, object] = {}
    run = GitHubAutomationRun(
        thread_id=uuid.uuid4(),
        input={"messages": []},
        context={"__run_token": "signed"},
        metadata={"channel": "github"},
    )

    async def fake_plan(
        event_type: str, payload: dict[str, object], delivery_id: str
    ) -> list[GitHubAutomationRun]:
        called["event_type"] = event_type
        called["payload"] = payload
        called["delivery_id"] = delivery_id
        return [run]

    async def reject_external_author(
        payload: dict[str, object], event_type: str
    ) -> dict[str, str] | None:
        called["gate_calls"] = int(called.get("gate_calls", 0)) + 1
        return {"status": "ignored", "reason": "Sender is not authorized"}

    monkeypatch.setattr(schedules, "plan_github_automations", fake_plan)
    monkeypatch.setattr(webhook_common, "enforce_public_repo_org_gate", reject_external_author)

    delivered = await _deliver(
        monkeypatch,
        "issues",
        {
            "action": "opened",
            "issue": {"number": 42, "title": "Public bug", "body": "Please investigate"},
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "outside-user"},
        },
        delivery_id="delivery-1",
    )

    # It doesn't mention Open SWE, so no run plans for it; MDA starts the automation's.
    assert delivered.work == ()
    assert delivered.issue_run is None
    assert delivered.automation_runs == (run,)
    assert called["event_type"] == "issues"
    assert called["delivery_id"] == "delivery-1"
    assert called.get("gate_calls", 0) == 0


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (
            "issue_comment",
            {
                "action": "created",
                "issue": {"number": 42, "pull_request": {"url": "u"}},
                "comment": {"body": "@openswe please handle this"},
            },
        ),
        (
            "pull_request_review_comment",
            {
                "action": "created",
                "pull_request": {"number": 42, "head": {"ref": "feature"}},
                "comment": {"id": 2, "in_reply_to_id": 1, "body": "The finding is wrong"},
            },
        ),
    ],
)
async def test_github_webhook_ignores_comments_from_unregistered_senders(
    monkeypatch, registry_db, event_type: str, payload: dict[str, object]
) -> None:
    handled = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_pr_comment", handled)
    monkeypatch.setattr(github_webhooks, "process_github_review_finding_reply", handled)
    register_github_logins(monkeypatch, "octocat")

    delivered = await _deliver(
        monkeypatch,
        event_type,
        {
            **payload,
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "stranger"},
        },
    )

    # The backend routes the comment, and an unregistered sender reaches no handler.
    assert "route_comment" in delivered.work
    handled.assert_not_awaited()


async def test_github_webhook_routes_review_comment_reply_without_tag(
    monkeypatch, registry_db
) -> None:
    called: dict[str, object] = {}
    auto_review_checked = False

    async def fake_process_github_review_finding_reply(payload: dict[str, object]) -> None:
        called["payload"] = payload

    async def fake_auto_review_enabled(_repo_config: dict[str, str]) -> bool:
        nonlocal auto_review_checked
        auto_review_checked = True
        return False

    monkeypatch.setattr(
        github_webhooks,
        "process_github_review_finding_reply",
        fake_process_github_review_finding_reply,
    )
    monkeypatch.setattr(webhook_common, "is_repo_auto_review_enabled", fake_auto_review_enabled)
    register_github_logins(monkeypatch, "octocat")

    delivered = await _deliver(
        monkeypatch,
        "pull_request_review_comment",
        {
            "action": "created",
            "comment": {
                "id": 222,
                "in_reply_to_id": 111,
                "body": "This is handled elsewhere, so the finding is invalid.",
            },
            "pull_request": {
                "number": 1244,
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha", "ref": "feature-branch"},
            },
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert delivered.work == ("route_comment",)
    assert auto_review_checked is False
    payload = called["payload"]
    assert isinstance(payload, dict)
    assert payload["comment"]["in_reply_to_id"] == 111


def _untagged_pr_event(
    event_type: str,
    sender: dict[str, str],
    *,
    action: str = "",
    number: int = 1244,
    body: str | None = None,
) -> dict[object, object]:
    repository = {"owner": {"login": "langchain-ai"}, "name": "open-swe"}
    if event_type == "issue_comment":
        return {
            "action": action or "created",
            "issue": {"number": number, "state": "open", "pull_request": {"url": "u"}},
            "comment": {"id": 5, "body": body if body is not None else "CI is failing on lint"},
            "repository": repository,
            "sender": sender,
        }
    return {
        "action": action or "submitted",
        "pull_request": {"number": number, "state": "open", "head": {"ref": "feature"}},
        "review": {"id": 6, "body": body or "", "state": "changes_requested"},
        "repository": repository,
        "sender": sender,
    }


@pytest.mark.parametrize(
    ("event_type", "action", "sender", "body", "accepted"),
    [
        ("issue_comment", "", {"login": "octocat"}, None, True),
        ("issue_comment", "edited", {"login": "octocat"}, None, True),
        ("issue_comment", "deleted", {"login": "octocat"}, None, False),
        ("issue_comment", "", {"login": "vercel[bot]"}, None, False),
        ("issue_comment", "", {"login": "open-swe[bot]"}, None, False),
        ("issue_comment", "", {"login": "stranger"}, None, False),
        ("issue_comment", "", {"login": "octocat"}, "!keep-open", False),
        ("issue_comment", "", {"login": "octocat"}, "@open-swe !keep-open", True),
        ("pull_request_review", "", {"login": "octocat"}, None, True),
        ("pull_request_review", "edited", {"login": "octocat"}, None, True),
        ("pull_request_review", "", {"login": "octocat"}, "!keep-open", True),
        ("pull_request_review", "", {"login": "devin-ai-integration[bot]"}, None, False),
        ("pull_request_review", "", {"login": "stranger"}, None, False),
    ],
)
async def test_github_webhook_wakes_agent_on_untagged_activity_on_its_pr(
    monkeypatch,
    registry_db,
    event_type: str,
    action: str,
    sender: dict[str, str],
    body: str | None,
    accepted: bool,
) -> None:
    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(
        payload: dict[str, object], event_type: str, *, agent_thread_id: str | None = None
    ) -> None:
        called["agent_thread_id"] = agent_thread_id

    async def allow(payload: dict[str, object], event_type: str) -> None:
        return None

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )
    monkeypatch.setattr(webhook_common, "enforce_public_repo_org_gate", allow)
    register_github_logins(monkeypatch, "octocat")
    await PullRequest(
        owner="langchain-ai",
        repo="open-swe",
        number=1244,
        opening_head_sha="head-sha",
        threads=[ThreadLink(thread_id="agent-thread", source=AGENT_OPENED_LINK_SOURCE)],
    ).save()

    delivered = await _deliver(
        monkeypatch, event_type, _untagged_pr_event(event_type, sender, action=action, body=body)
    )

    expected_thread_id = None if webhook_common.mentions_open_swe(body) else "agent-thread"
    assert called == ({"agent_thread_id": expected_thread_id} if accepted else {})
    if body is not None and not accepted:
        # An untagged workflow command reaches the backend as no work at all.
        assert "route_comment" not in delivered.work


@pytest.mark.parametrize(
    ("source", "opening_head_sha"),
    [("github_pr_comment", "head-sha"), (AGENT_OPENED_LINK_SOURCE, "")],
    ids=["commented-only", "linked-not-opened"],
)
async def test_github_webhook_ignores_untagged_comment_on_pr_agent_did_not_open(
    monkeypatch, registry_db, source: str, opening_head_sha: str
) -> None:
    handled = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_pr_comment", handled)
    register_github_logins(monkeypatch, "octocat")
    await PullRequest(
        owner="langchain-ai",
        repo="open-swe",
        number=1244,
        opening_head_sha=opening_head_sha,
        threads=[ThreadLink(thread_id="other-thread", source=source)],
    ).save()

    await _deliver(
        monkeypatch, "issue_comment", _untagged_pr_event("issue_comment", {"login": "octocat"})
    )

    handled.assert_not_awaited()


def test_process_github_review_finding_reply_dispatches_sanitized_reply_body(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_thread_metadata_safe(_thread_id: str) -> dict[str, object]:
        return {"kind": webhook_common.REVIEWER_THREAD_KIND}

    async def fake_get_token_with_expiry() -> tuple[str, str]:
        return "app-token", "2026-01-01T00:00:00Z"

    def fake_cache_token(_thread_id: str, _token: str, *, expires_at: str | None = None) -> None:
        captured["expires_at"] = expires_at

    async def fake_fetch_threads(**_kwargs: object) -> list[dict[str, object]]:
        return []

    async def fake_reconcile(_thread_id: str, _threads: list[dict[str, object]]) -> None:
        return None

    async def fake_list_findings(_thread_id: str) -> list[dict[str, object]]:
        return [{"id": "f_1", "github_review_comment_id": 111}]

    async def fake_append_interaction(
        _thread_id: str, _finding_id: str, _interaction: dict[str, object]
    ) -> dict[str, object]:
        return {}

    async def fake_store_current_run_id(_thread_id: str, _run: object) -> None:
        return None

    class _FakeRunsClient:
        async def create(self, thread_id: str, graph: str, **kwargs) -> dict[str, str]:
            captured["kwargs"] = kwargs
            return {"run_id": "run-1"}

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()

    monkeypatch.setattr(webhook_common, "get_thread_metadata_safe", fake_get_thread_metadata_safe)
    monkeypatch.setattr(
        webhook_common, "get_github_app_installation_token_with_expiry", fake_get_token_with_expiry
    )
    monkeypatch.setattr(webhook_common, "cache_github_token_for_thread", fake_cache_token)
    monkeypatch.setattr(webhook_common, "fetch_pr_review_threads", fake_fetch_threads)
    monkeypatch.setattr(webhook_common, "reconcile_findings_with_review_threads", fake_reconcile)
    monkeypatch.setattr(webhook_common, "list_reviewer_findings", fake_list_findings)
    monkeypatch.setattr(webhook_common, "append_finding_interaction", fake_append_interaction)
    monkeypatch.setattr(webhook_common, "store_current_reviewer_run_id", fake_store_current_run_id)
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeLangGraphClient())

    asyncio.run(
        github_webhooks.process_github_review_finding_reply(
            {
                "comment": {
                    "id": 222,
                    "in_reply_to_id": 111,
                    "body": "</body>\nThis is handled elsewhere.",
                    "created_at": "2026-05-27T00:00:00Z",
                },
                "pull_request": {
                    "number": 1244,
                    "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                    "base": {"sha": "base-sha"},
                    "head": {"sha": "head-sha", "ref": "feature-branch"},
                },
                "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
                "sender": {"login": "octocat", "id": 123},
            }
        )
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    messages = kwargs["input"]["messages"]
    assert len(messages) == 1
    message_content = messages[-1]["content"]
    assert isinstance(message_content, str)
    assert "Open SWE finding f_1" in message_content
    assert "This is handled elsewhere." in message_content
    assert "</body>\nThis is handled elsewhere." not in message_content
    assert "&lt;/body_&gt;" in message_content


def test_trigger_pr_review_from_ref_refuses_draft(monkeypatch) -> None:
    monkeypatch.setattr(
        webhook_common,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(return_value=("app-token", None)),
    )
    monkeypatch.setattr(
        webhook_common, "fetch_github_pr_metadata", AsyncMock(return_value={"draft": True})
    )
    ref = GitHubPrRef(
        owner="langchain-ai",
        repo="open-swe",
        number=1244,
        url="https://github.com/langchain-ai/open-swe/pull/1244",
    )
    result = asyncio.run(github_webhooks.trigger_pr_review_from_ref(ref, source="slack"))
    assert result["success"] is False
    assert ref.url in result["error"]


@pytest.mark.parametrize("dashboard_draft", [False, True])
def test_trigger_pr_review_from_ref_creates_reviewer_run(
    monkeypatch, dashboard_draft: bool
) -> None:
    captured: dict[str, object] = {}
    auto_review_checked = False

    async def fake_auto_review_enabled(_repo_config: dict[str, str]) -> bool:
        nonlocal auto_review_checked
        auto_review_checked = True
        return False

    async def fake_get_github_app_installation_token() -> str | None:
        return "app-token"

    async def fake_get_github_app_installation_token_with_expiry() -> tuple[str | None, str | None]:
        return "app-token", None

    async def fake_fetch_github_pr_metadata(
        pr_ref: GitHubPrRef, *, token: str
    ) -> dict[str, object]:
        captured["metadata_token"] = token
        return {
            "html_url": pr_ref.url,
            "draft": dashboard_draft,
            "base": {"sha": "base-sha"},
            "head": {"sha": "head-sha", "ref": "feature-branch"},
        }

    def fake_cache_github_token(
        thread_id: str, token: str, *, expires_at: str | None = None
    ) -> None:
        captured["cache_thread_id"] = thread_id
        captured["cache_token"] = token
        captured["cache_expires_at"] = expires_at

    class _FakeRunsClient:
        async def create(self, thread_id: str, graph: str, **kwargs) -> None:
            captured["thread_id"] = thread_id
            captured["graph"] = graph
            captured["kwargs"] = kwargs

    class _FakeThreadsClient:
        async def create(self, **kwargs) -> dict[str, object]:
            captured["thread_create_kwargs"] = kwargs
            return {"thread_id": kwargs["thread_id"], "metadata": kwargs["metadata"]}

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()
        threads = _FakeThreadsClient()

    async def fake_set_reviewer_thread_metadata(thread_id: str, **kwargs: object) -> None:
        captured["set_metadata_thread_id"] = thread_id
        captured["set_metadata_kwargs"] = kwargs

    monkeypatch.setattr(webhook_common, "is_repo_auto_review_enabled", fake_auto_review_enabled)
    monkeypatch.setattr(
        webhook_common, "get_github_app_installation_token", fake_get_github_app_installation_token
    )
    monkeypatch.setattr(
        webhook_common,
        "get_github_app_installation_token_with_expiry",
        fake_get_github_app_installation_token_with_expiry,
    )

    async def fake_post_review_started_comment(**kwargs: object) -> int:
        captured["status_comment_kwargs"] = kwargs
        return 1

    monkeypatch.setattr(webhook_common, "fetch_github_pr_metadata", fake_fetch_github_pr_metadata)
    monkeypatch.setattr(webhook_common, "cache_github_token_for_thread", fake_cache_github_token)
    monkeypatch.setattr(
        webhook_common, "set_reviewer_thread_metadata", fake_set_reviewer_thread_metadata
    )
    monkeypatch.setattr(
        webhook_common, "post_review_started_comment", fake_post_review_started_comment
    )
    monkeypatch.setattr(webhook_common, "get_client", lambda url: _FakeLangGraphClient())

    result = asyncio.run(
        trigger_re_review("langchain-ai", "open-swe", 1244, "octocat")
        if dashboard_draft
        else github_webhooks.trigger_pr_review_from_ref(
            GitHubPrRef(
                owner="langchain-ai",
                repo="open-swe",
                number=1244,
                url="https://github.com/langchain-ai/open-swe/pull/1244",
            ),
            source="slack",
            slack_channel_id="C123",
            slack_thread_ts="1700000000.000100",
        )
    )

    kwargs = cast(dict[str, object], captured["kwargs"])
    input_data = cast(dict[str, object], kwargs["input"])
    prompt = cast(list[dict[str, str]], input_data["messages"])[-1]["content"]
    config = cast(dict[str, object], cast(dict[str, object], kwargs["config"])["configurable"])
    assert result["success"] is True
    assert auto_review_checked is False
    assert captured["graph"] == "reviewer"
    assert captured["thread_create_kwargs"] == {
        "thread_id": captured["thread_id"],
        "if_exists": "do_nothing",
        "metadata": {"title": "Review: #1244"},
    }
    assert captured["metadata_token"] == "app-token"
    assert "\nbase_sha: base-sha\n" in prompt
    assert "\nhead_sha: head-sha\n" in prompt
    assert config["source"] == ("dashboard" if dashboard_draft else "slack")
    assert config["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert config["pr_number"] == 1244
    assert config["review_requested"] is True
    if dashboard_draft:
        assert "slack_thread" not in config
    else:
        assert config["slack_thread"] == {
            "channel_id": "C123",
            "thread_ts": "1700000000.000100",
        }
    # The live head must be persisted to metadata so resolve_review_head_sha
    # doesn't return a stale head left by a prior push/ready dispatch.
    metadata_kwargs = cast(dict[str, object], captured["set_metadata_kwargs"])
    assert metadata_kwargs["head_sha"] == "head-sha"
    # A live status comment is posted on dispatch so the PR shows "reviewing".
    status_comment_kwargs = cast(dict[str, object], captured["status_comment_kwargs"])
    assert status_comment_kwargs["pr_number"] == 1244


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "body", "inline_feedback", "tagged", "should_dispatch"),
    [
        pytest.param("approved", "", False, False, False, id="empty-approval"),
        pytest.param("approved", None, False, False, False, id="null-approval"),
        pytest.param("approved", " \n\t", False, False, False, id="whitespace-approval"),
        pytest.param("approved", "", True, False, True, id="approval-inline-feedback"),
        pytest.param("approved", "Please rename this", False, False, True, id="approval-body"),
        pytest.param("approved", "", True, True, True, id="tagged-inline-approval"),
        pytest.param("approved", "", False, True, True, id="explicit-tagged-path"),
        pytest.param("changes_requested", "", False, False, True, id="changes-requested"),
    ],
)
async def test_process_github_pr_review_skips_only_empty_untagged_approvals(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    body: str | None,
    inline_feedback: bool,
    tagged: bool,
    should_dispatch: bool,
) -> None:
    thread_id = "00000000-0000-0000-0000-000000000001"
    comments: list[dict[str, object]] = [
        {
            "type": "review",
            "comment_id": 9,
            "body": body or f"_Submitted a review: {state}_",
            "author": "octocat",
            "created_at": "2026-09-28T15:05:34Z",
        }
    ]
    if inline_feedback:
        comments.append(
            {
                "type": "review_comment",
                "comment_id": 10,
                "review_id": 9,
                "body": "@open-swe please rename this" if tagged else "Please rename this",
                "author": "octocat",
                "created_at": "2026-09-28T15:05:30Z",
                "path": "agent/server.py",
                "line": 42,
            }
        )
    fetch_comments = AsyncMock(return_value=comments)
    react = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(
        webhook_common,
        "extract_pr_context",
        AsyncMock(
            return_value=(
                {"owner": "langchain-ai", "name": "open-swe"},
                3127,
                f"open-swe/{thread_id}",
                "octocat",
                "https://github.com/langchain-ai/open-swe/pull/3127",
                9,
                None,
            )
        ),
    )
    monkeypatch.setattr(PullRequest, "link_thread", AsyncMock())
    monkeypatch.setattr(webhook_common, "get_thread_metadata_safe", AsyncMock(return_value={}))
    monkeypatch.setattr(webhook_common, "authorize_github_thread", AsyncMock(return_value={}))
    monkeypatch.setattr(
        webhook_common, "get_or_resolve_thread_github_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(webhook_common, "react_to_github_comment", react)
    monkeypatch.setattr(webhook_common, "fetch_pr_event_comments", fetch_comments)
    monkeypatch.setattr(webhook_common, "fetch_pr_comments_since_last_tag", fetch_comments)
    monkeypatch.setattr(webhook_common, "trigger_or_queue_run", dispatch)
    monkeypatch.setattr(User, "email_for_login", AsyncMock(return_value="octocat@example.com"))
    monkeypatch.setattr(User, "known_logins", AsyncMock(return_value=frozenset({"octocat"})))
    monkeypatch.setattr(github_webhooks.postgres, "configured", lambda: False)

    await github_webhooks.process_github_pr_comment(
        {
            "action": "submitted",
            "sender": {"login": "octocat", "id": 123},
            "pull_request": {"user": {"login": "mdrxy"}},
            "review": {
                "id": 9,
                "state": state,
                "body": body,
                "user": {"login": "octocat"},
                "submitted_at": "2026-09-28T15:05:34Z",
            },
        },
        "pull_request_review",
        agent_thread_id=None if tagged else thread_id,
    )

    fetch_comments.assert_awaited_once()
    if should_dispatch:
        react.assert_awaited_once()
        dispatch.assert_awaited_once()
        assert dispatch.call_args.args[0] == thread_id
    else:
        react.assert_not_awaited()
        dispatch.assert_not_awaited()


def test_a_github_issue_followup_keeps_the_threads_workspace(monkeypatch) -> None:
    """A follow-up lands in the thread's workspace even if the repository is preferred elsewhere."""

    async def fake_react_to_github_comment(*args: object, **kwargs: object) -> bool:
        return True

    monkeypatch.setattr(
        webhook_common,
        "get_or_resolve_thread_github_token",
        lambda thread_id, email: asyncio.sleep(0, result="user-token"),
    )
    monkeypatch.setattr(
        webhook_common, "get_github_app_installation_token", lambda: asyncio.sleep(0, result=None)
    )
    monkeypatch.setattr(github_webhooks, "_conversation_started", AsyncMock(return_value=True))
    monkeypatch.setattr(
        webhook_common, "get_thread_workspace", lambda thread_id: asyncio.sleep(0, result="core")
    )
    monkeypatch.setattr(
        webhook_common, "workspace_for_repo_config", lambda repo: asyncio.sleep(0, result="oss")
    )
    monkeypatch.setattr(webhook_common, "react_to_github_comment", fake_react_to_github_comment)
    monkeypatch.setattr(
        webhook_common,
        "upsert_agent_thread_metadata",
        lambda *a, **k: asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        User, "email_for_login", lambda login: asyncio.sleep(0, result="octocat@example.com")
    )
    monkeypatch.setattr(
        User, "known_logins", lambda logins: asyncio.sleep(0, result=frozenset({"octocat"}))
    )

    run = asyncio.run(
        github_webhooks.prepare_github_issue_run(
            {
                "issue": {
                    "id": 12345,
                    "number": 42,
                    "title": "Fix the flaky test",
                    "body": "The test is failing intermittently.",
                    "html_url": "https://github.com/langchain-ai/open-swe/issues/42",
                },
                "comment": {
                    "id": 999,
                    "body": "@openswe please handle this",
                    "user": {"login": "octocat"},
                },
                "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
                "sender": {"login": "octocat"},
            },
            "issue_comment",
            "thread-1",
        )
    )

    assert run is not None
    assert run.configurable["workspace"] == "core"


def _thread_not_on_mda() -> NotFoundError:
    response = httpx2.Response(404, request=httpx2.Request("GET", "http://test"))
    return NotFoundError("not found", response=response, body=None)


@pytest.mark.parametrize(
    ("state", "started"),
    [
        (_thread_not_on_mda(), False),
        # Adopted by a delivery whose run never took a turn: a retry still starts afresh.
        ({"values": {"messages": []}}, False),
        ({"values": {"messages": [{"type": "human", "content": "Fix it"}]}}, True),
    ],
)
async def test_an_issue_thread_continues_only_once_a_run_took_the_issue(
    monkeypatch: pytest.MonkeyPatch, state: object, started: bool
) -> None:
    read = (
        AsyncMock(side_effect=state)
        if isinstance(state, Exception)
        else AsyncMock(return_value=state)
    )
    monkeypatch.setattr(github_webhooks.THREADS, "state", read)

    assert await github_webhooks._conversation_started("thread-1") is started


@pytest.mark.parametrize(
    ("private", "persisted", "scope", "prepared"),
    [
        (False, True, ["langchain-ai/open-swe"], True),
        (None, True, ["langchain-ai/open-swe"], True),
        (True, True, None, True),
        (False, False, ["langchain-ai/open-swe"], False),
    ],
)
def test_a_new_issue_thread_on_a_public_repository_records_a_single_repository_scope(
    monkeypatch, private: bool | None, persisted: bool, scope: list[str] | None, prepared: bool
) -> None:
    captured: dict[str, object] = {}

    async def fake_upsert(thread_id: str, **kwargs: object) -> bool:
        captured["token_repositories"] = kwargs.get("token_repositories")
        return persisted

    async def fake_react_to_github_comment(*args: object, **kwargs: object) -> bool:
        return True

    async def fake_fetch_issue_comments(*args: object, **kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(
        webhook_common,
        "get_or_resolve_thread_github_token",
        lambda thread_id, email: asyncio.sleep(0, result="user-token"),
    )
    monkeypatch.setattr(
        webhook_common, "get_github_app_installation_token", lambda: asyncio.sleep(0, result=None)
    )
    monkeypatch.setattr(github_webhooks, "_conversation_started", AsyncMock(return_value=False))
    monkeypatch.setattr(
        webhook_common, "workspace_for_repo_config", lambda repo: asyncio.sleep(0, result="oss")
    )
    monkeypatch.setattr(webhook_common, "upsert_agent_thread_metadata", fake_upsert)
    monkeypatch.setattr(webhook_common, "react_to_github_comment", fake_react_to_github_comment)
    monkeypatch.setattr(webhook_common, "fetch_issue_comments", fake_fetch_issue_comments)
    monkeypatch.setattr(
        User, "email_for_login", lambda login: asyncio.sleep(0, result="octocat@example.com")
    )
    monkeypatch.setattr(User, "known_logins", lambda logins: asyncio.sleep(0, result=frozenset()))
    repository: dict[str, object] = {"owner": {"login": "langchain-ai"}, "name": "open-swe"}
    if private is not None:
        repository["private"] = private

    run = asyncio.run(
        github_webhooks.prepare_github_issue_run(
            {
                "issue": {
                    "id": 12345,
                    "number": 42,
                    "title": "Fix the flaky test",
                    "body": "@openswe please handle this",
                    "html_url": "https://github.com/langchain-ai/open-swe/issues/42",
                },
                "repository": repository,
                "sender": {"login": "octocat"},
            },
            "issues",
            "thread-1",
        )
    )

    assert captured["token_repositories"] == scope
    assert (run is not None) is prepared
