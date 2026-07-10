"""Tests for Linear webhook PR author linking (reuse of the Slack user mapping)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from agent.webhooks import linear as linear_webhook


def _full_issue(*, user_email: str | None = "zhen@example.com", user_name: str = "Zhen") -> dict:
    return {
        "id": "issue-1",
        "title": "Link Linear PRs to author",
        "description": "Do the thing",
        "identifier": "OS-42",
        "url": "https://linear.app/x/issue/OS-42",
        "creator": {"email": user_email, "name": user_name},
        "comments": {"nodes": []},
    }


def _issue_data(*, user_email: str | None, user_name: str = "Zhen") -> dict:
    # linear_webhook attaches comment_author to the issue dict before dispatch.
    data = _full_issue(user_email=user_email, user_name=user_name)
    data["comment_author"] = {"email": user_email, "name": user_name}
    return data


def _run_process(
    issue_data: dict, repo_config: dict[str, str]
) -> tuple[dict, dict, str | None, object]:
    captured: dict[str, Any] = {}

    async def fake_dispatch(
        thread_id, content, configurable, *, source, metadata=None, client=None
    ):
        captured["content"] = content
        captured["configurable"] = configurable
        return {"run_id": "run-1"}

    async def fake_upsert(
        thread_id,
        *,
        source,
        repo_config=None,
        github_login="",
        user_email="",
        title="",
        source_context=None,
    ):
        captured["upsert"] = {"github_login": github_login, "user_email": user_email}
        return None

    async def fake_resolve_login(email):
        captured["resolved_email"] = email
        return "zhen" if email == "zhen@example.com" else None

    with (
        patch.object(linear_webhook.common, "react_to_linear_comment", new_callable=AsyncMock),
        patch.object(
            linear_webhook.common, "generate_thread_id_from_issue", return_value="thread-1"
        ),
        patch.object(
            linear_webhook.common,
            "fetch_linear_issue_details",
            new_callable=AsyncMock,
            return_value=_full_issue(user_email=issue_data.get("comment_author", {}).get("email")),
        ),
        patch.object(
            linear_webhook.common, "resolve_login_from_email_async", side_effect=fake_resolve_login
        ),
        patch.object(linear_webhook.common, "dispatch_agent_run", side_effect=fake_dispatch),
        patch.object(
            linear_webhook.common, "upsert_agent_thread_owner_metadata", side_effect=fake_upsert
        ),
        patch.object(linear_webhook.common, "post_linear_trace_comment", new_callable=AsyncMock),
    ):
        asyncio.run(linear_webhook.process_linear_issue(issue_data, repo_config))

    return (
        captured.get("configurable", {}),
        captured.get("upsert", {}),
        captured.get("resolved_email"),
        captured.get("content"),
    )


def test_linear_configurable_carries_github_login() -> None:
    configurable, _upsert, resolved_email, _content = _run_process(
        _issue_data(user_email="zhen@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    assert resolved_email == "zhen@example.com"
    assert configurable["source"] == "linear"
    assert configurable["github_login"] == "zhen"
    assert configurable["user_email"] == "zhen@example.com"


def test_linear_upsert_tags_thread_with_login() -> None:
    _configurable, upsert, _email, _content = _run_process(
        _issue_data(user_email="zhen@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    assert upsert["github_login"] == "zhen"
    assert upsert["user_email"] == "zhen@example.com"


def test_linear_omits_login_when_unmapped() -> None:
    configurable, upsert, resolved_email, _content = _run_process(
        _issue_data(user_email="nobody@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    assert resolved_email == "nobody@example.com"
    assert "github_login" not in configurable
    assert upsert["github_login"] == ""


def test_linear_issue_prompt_mentions_pr_references_and_conventions() -> None:
    _configurable, _upsert, _email, content = _run_process(
        _issue_data(user_email="zhen@example.com"),
        {"owner": "langchain-ai", "name": "open-swe"},
    )

    prompt = content[0]["text"]
    assert "https://linear.app/x/issue/OS-42" in prompt
    assert "PR description links back to this Linear ticket" in prompt
    assert "repository's PR conventions" in prompt
    assert ".changelog/README.md" in prompt
