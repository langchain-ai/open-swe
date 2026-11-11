"""Tests for the public-repo org-membership gate on GitHub webhooks."""

from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest

from openswe.github import webhook as github_webhooks
from openswe.webhooks import common as webhook_common
from tests.conftest import register_github_logins
from tests.github_channel import deliver_github_event


def _install_membership_stub(monkeypatch, members: set[str]) -> dict[str, list[str]]:
    """Stub the org-membership lookup to return True only for ``members``."""
    seen: dict[str, list[str]] = {"calls": []}

    async def fake_is_user_active_org_member(username: str, org: str) -> bool:
        seen["calls"].append(username)
        return username in members

    monkeypatch.setattr(webhook_common, "is_user_active_org_member", fake_is_user_active_org_member)
    return seen


def _common_setup(monkeypatch, *, gate: str = "langchain-ai") -> None:
    monkeypatch.setattr(webhook_common, "PUBLIC_REPO_ORG_GATE", gate)
    monkeypatch.setattr(webhook_common, "ALLOWED_GITHUB_ORGS", frozenset())
    register_github_logins(monkeypatch, "stranger", "insider")


@pytest.mark.parametrize("sender", ["Configured[bot]", "CONFIGURED[BOT]"])
async def test_gate_recognizes_internal_bots_case_insensitively(
    monkeypatch: pytest.MonkeyPatch, sender: str
) -> None:
    monkeypatch.setattr(webhook_common, "PUBLIC_REPO_ORG_GATE", "langchain-ai")
    monkeypatch.setattr(webhook_common, "INTERNAL_BOT_LOGINS", frozenset({"configured[bot]"}))
    assert await webhook_common._is_sender_allowed_for_public_repo(
        {"repository": {"private": False}, "sender": {"login": sender}}
    )


async def test_gate_blocks_non_member_on_public_pr_comment(monkeypatch, registry_db) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members={"insider"})

    handled = AsyncMock()
    monkeypatch.setattr(github_webhooks, "process_github_pr_comment", handled)

    await deliver_github_event(
        monkeypatch,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe review"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    handled.assert_not_awaited()
    assert seen["calls"] == ["stranger"]


async def test_gate_allows_org_member_on_public_pr_comment(monkeypatch, registry_db) -> None:
    _common_setup(monkeypatch)
    _install_membership_stub(monkeypatch, members={"insider"})

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type, **_kwargs) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    await deliver_github_event(
        monkeypatch,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe review"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "insider", "type": "User"},
        },
    )

    assert called["event"] == "issue_comment"


async def test_gate_skipped_on_private_repo(monkeypatch, registry_db) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members=set())

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type, **_kwargs) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    await deliver_github_event(
        monkeypatch,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe please look at this"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": True,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert called["event"] == "issue_comment"
    assert seen["calls"] == []


async def test_gate_disabled_when_env_unset(monkeypatch, registry_db) -> None:
    _common_setup(monkeypatch, gate="")
    seen = _install_membership_stub(monkeypatch, members=set())

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type, **_kwargs) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    await deliver_github_event(
        monkeypatch,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe please look at this"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert called["event"] == "issue_comment"
    assert seen["calls"] == []


async def test_gate_blocks_non_member_on_public_issue(monkeypatch, registry_db) -> None:
    _common_setup(monkeypatch)
    _install_membership_stub(monkeypatch, members={"insider"})

    prepare = AsyncMock()
    monkeypatch.setattr(github_webhooks, "prepare_github_issue_run", prepare)
    monkeypatch.setattr(github_webhooks, "conversation_started", AsyncMock(return_value=False))

    delivered = await deliver_github_event(
        monkeypatch,
        "issues",
        {
            "action": "opened",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "@openswe please help",
                "body": "x",
            },
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert delivered.issue_run is not None
    assert delivered.issue_run.starts is False
    prepare.assert_not_awaited()


async def test_review_requested_starts_no_review_before_public_repo_gate(
    monkeypatch, registry_db
) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members={"insider"})

    delivered = await deliver_github_event(
        monkeypatch,
        "pull_request",
        {
            "action": "review_requested",
            "requested_reviewer": {"login": "open-swe[bot]"},
            "pull_request": {
                "number": 1244,
                "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha", "ref": "feature-branch"},
            },
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert delivered.work == ("wake_merge_queue",)
    assert seen["calls"] == []


async def test_internal_bot_comment_never_prompts_the_agent(monkeypatch, registry_db) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members=set())

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type, **_kwargs) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    await deliver_github_event(
        monkeypatch,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe please look at this"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "open-swe[bot]", "type": "Bot"},
        },
    )

    assert called == {}
    assert seen["calls"] == []


def _org_answers(
    monkeypatch, membership: httpx2.Response, token: str | None = "x"
) -> dict[str, Any]:
    """Serve the org's installation, its token and one membership answer."""
    from openswe.github import app as github_app
    from openswe.github import http as github_http

    minted: dict[str, Any] = {}

    async def installation(_org: str) -> int:
        return 1

    async def mint(**kwargs: Any) -> str | None:
        minted.update(kwargs)
        return token

    monkeypatch.setattr(github_app, "get_github_app_installation_id_for_org", installation)
    monkeypatch.setattr(github_app, "get_github_app_installation_token", mint)
    monkeypatch.setattr(
        github_http, "GITHUB_TRANSPORT", httpx2.MockTransport(lambda _request: membership)
    )
    return minted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("membership", "token", "member"),
    [
        (httpx2.Response(200, json={"state": "active"}), None, False),
        (httpx2.Response(404, json={"message": "Not Found"}), "x", False),
        (httpx2.Response(200, json={"state": "pending"}), "x", False),
        (httpx2.Response(200, json={"state": "active"}), "x", True),
    ],
)
async def test_is_user_active_org_member(
    monkeypatch, membership: httpx2.Response, token: str | None, member: bool
) -> None:
    from openswe.github import org_membership as github_org_membership

    minted = _org_answers(monkeypatch, membership, token)

    assert await github_org_membership.is_user_active_org_member("alice", "langchain-ai") is member
    assert minted["permissions"] == {"members": "read"}
