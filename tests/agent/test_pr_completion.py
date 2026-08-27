from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import pr_completion
from agent.pr_completion import PRCompletionWatch
from agent.source_context import SourceContext
from agent.utils import slack as slack_utils


def _watch() -> PRCompletionWatch:
    return PRCompletionWatch(
        key="thread-1",
        thread_id="thread-1",
        owner="acme",
        repo="widgets",
        pr_number=7,
        pr_url="https://github.com/acme/widgets/pull/7",
        head_sha="abc123",
        head_ref="open-swe/change",
        installation_id=42,
        source_context=SourceContext.parse(
            {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}
        ),
        deferred_message="Implemented the change.",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_success_webhook_posts_deferred_message_when_complete_set_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch = _watch()

    @asynccontextmanager
    async def lock(_key: str) -> AsyncIterator[bool]:
        yield True

    monkeypatch.setattr(pr_completion, "_watch_lock", lock)
    monkeypatch.setattr(pr_completion.WATCHES, "list_active", AsyncMock(return_value=[watch]))
    monkeypatch.setattr(pr_completion.WATCHES, "get", AsyncMock(return_value=watch))
    delete = AsyncMock()
    monkeypatch.setattr(pr_completion.WATCHES, "delete", delete)
    monkeypatch.setattr(
        pr_completion, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        pr_completion,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "abc123"}}),
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    github_client = MagicMock(return_value=client)
    monkeypatch.setattr(pr_completion, "github_client", github_client)
    monkeypatch.setattr(
        pr_completion,
        "_fetch_checks",
        AsyncMock(
            return_value={
                "headSha": "abc123",
                "rollupState": "SUCCESS",
                "checks": [],
                "truncated": False,
            }
        ),
    )
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_utils, "post_slack_thread_reply", post)

    result = await pr_completion.handle_ci_webhook(
        {
            "repository": {"owner": {"login": "acme"}, "name": "widgets"},
            "check_run": {
                "status": "completed",
                "conclusion": "success",
                "head_sha": "abc123",
                "check_suite": {"head_branch": "open-swe/change"},
            },
        },
        "check_run",
    )

    assert result == {"matched": 1, "notified": 1}
    post.assert_awaited_once_with(
        "C1",
        "1.0",
        "Implemented the change. https://github.com/acme/widgets/pull/7",
        agent_thread_id="thread-1",
        bypass_pr_completion_gate=True,
    )
    delete.assert_awaited_once_with("thread-1")


@pytest.mark.asyncio
async def test_success_webhook_stays_silent_while_any_check_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch = _watch()

    @asynccontextmanager
    async def lock(_key: str) -> AsyncIterator[bool]:
        yield True

    monkeypatch.setattr(pr_completion, "_watch_lock", lock)
    monkeypatch.setattr(pr_completion.WATCHES, "list_active", AsyncMock(return_value=[watch]))
    monkeypatch.setattr(pr_completion.WATCHES, "get", AsyncMock(return_value=watch))
    monkeypatch.setattr(
        pr_completion, "get_github_app_installation_token", AsyncMock(return_value="token")
    )
    monkeypatch.setattr(
        pr_completion,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "abc123"}}),
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    github_client = MagicMock(return_value=client)
    monkeypatch.setattr(pr_completion, "github_client", github_client)
    monkeypatch.setattr(
        pr_completion,
        "_fetch_checks",
        AsyncMock(
            return_value={
                "headSha": "abc123",
                "rollupState": "PENDING",
                "checks": [{"name": "CI", "status": "IN_PROGRESS", "required": True}],
                "truncated": False,
            }
        ),
    )
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_utils, "post_slack_thread_reply", post)

    result = await pr_completion.handle_ci_webhook(
        {
            "repository": {"owner": {"login": "acme"}, "name": "widgets"},
            "status": {"state": "success"},
            "state": "success",
            "sha": "abc123",
            "branches": [{"name": "open-swe/change"}],
        },
        "status",
    )

    assert result == {"matched": 1, "notified": 0}
    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_deferred_message_posts_immediately_if_green_event_arrived_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch = _watch()
    watch.deferred_message = ""
    watch.green_head_sha = watch.head_sha

    @asynccontextmanager
    async def lock(_key: str) -> AsyncIterator[bool]:
        yield True

    monkeypatch.setattr(pr_completion, "_watch_lock", lock)
    monkeypatch.setattr(pr_completion.WATCHES, "list_active", AsyncMock(return_value=[watch]))
    monkeypatch.setattr(pr_completion.WATCHES, "get", AsyncMock(return_value=watch))
    delete = AsyncMock()
    monkeypatch.setattr(pr_completion.WATCHES, "delete", delete)
    post = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_utils, "post_slack_thread_reply", post)

    result = await pr_completion.defer_message("thread-1", "Implemented the change.")

    assert result is watch
    post.assert_awaited_once()
    delete.assert_awaited_once_with("thread-1")


@pytest.mark.asyncio
async def test_non_success_webhook_does_not_read_watches(monkeypatch: pytest.MonkeyPatch) -> None:
    list_active = AsyncMock()
    monkeypatch.setattr(pr_completion.WATCHES, "list_active", list_active)

    result = await pr_completion.handle_ci_webhook(
        {"check_run": {"status": "completed", "conclusion": "failure"}}, "check_run"
    )

    assert result == {"matched": 0, "notified": 0}
    list_active.assert_not_awaited()
