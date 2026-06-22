"""Tests for FastAPI webapp routes and helpers."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import webapp


@pytest.mark.asyncio
async def test_process_slack_mention_dedupes_concurrent_events_on_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent webhook deliveries on one thread create exactly one run."""
    webapp._slack_thread_locks.clear()
    webapp._slack_processed_events.clear()

    async def _set_status(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def _refresh_cache() -> None:
        return None

    async def _slack_user(_user_id: str) -> dict[str, Any]:
        return {"profile": {"email": "u@example.com", "display_name": "U"}}

    async def _fetch_thread(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def _select_ctx(*_args: Any, **_kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        return ([], "thread_start")

    async def _user_names(_ids: list[str]) -> dict[str, str]:
        return {}

    def _format(*_args: Any, **_kwargs: Any) -> str:
        return ""

    async def _resolve_links(*_args: Any, **_kwargs: Any) -> tuple[str, list[str]]:
        return ("", [])

    async def _login_for_slack(_user_id: str) -> str:
        return "octocat"

    async def _login_for_email(_email: str) -> str:
        return "octocat"

    async def _valid_token(_login: str) -> str:
        return "ghp_test"

    async def _thread_exists(_thread_id: str) -> bool:
        return False

    async def _upsert_repo(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _upsert_owner(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _is_active(_thread_id: str) -> bool:
        return False

    async def _post_trace(*_args: Any, **_kwargs: Any) -> str:
        return "1.2"

    async def _store_mapping(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(webapp, "set_slack_assistant_status", _set_status)
    monkeypatch.setattr(webapp, "refresh_user_mapping_cache", _refresh_cache)
    monkeypatch.setattr(webapp, "get_slack_user_info", _slack_user)
    monkeypatch.setattr(webapp, "fetch_slack_thread_messages", _fetch_thread)
    monkeypatch.setattr(webapp, "select_slack_context_messages", _select_ctx)
    monkeypatch.setattr(webapp, "get_slack_user_names", _user_names)
    monkeypatch.setattr(webapp, "format_slack_messages_for_prompt", _format)
    monkeypatch.setattr(webapp, "resolve_slack_links_in_context", _resolve_links)
    monkeypatch.setattr(webapp, "strip_bot_mention", lambda text, *_args, **_kwargs: text)
    monkeypatch.setattr(webapp, "dedupe_urls", lambda urls: urls)
    monkeypatch.setattr(webapp, "extract_image_urls", lambda _text: [])
    monkeypatch.setattr(webapp, "login_for_slack_id", _login_for_slack)
    monkeypatch.setattr(webapp, "login_for_email", _login_for_email)
    monkeypatch.setattr(webapp, "get_valid_access_token", _valid_token)
    monkeypatch.setattr(webapp, "is_bot_token_only_mode", lambda: True)
    monkeypatch.setattr(webapp, "_thread_exists", _thread_exists)
    monkeypatch.setattr(webapp, "_upsert_slack_thread_repo_metadata", _upsert_repo)
    monkeypatch.setattr(webapp, "upsert_agent_thread_owner_metadata", _upsert_owner)
    monkeypatch.setattr(webapp, "is_thread_active", _is_active)
    monkeypatch.setattr(webapp, "post_slack_trace_reply", _post_trace)
    monkeypatch.setattr(webapp, "store_slack_run_mapping", _store_mapping)

    runs_create = AsyncMock(return_value={"run_id": "run-1"})
    fake_client = MagicMock()
    fake_client.runs.create = runs_create
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)

    event_data = {
        "channel_id": "C123",
        "thread_ts": "1782136385.915719",
        "event_ts": "1782136385.915719",
        "user_id": "U123",
        "text": "hello",
        "bot_user_id": "BOT",
    }
    repo_config = {"owner": "octo", "name": "repo"}

    # Same event_ts (true retry-style duplicate) — must collapse to one run.
    await asyncio.gather(
        webapp.process_slack_mention(event_data, repo_config),
        webapp.process_slack_mention(event_data, repo_config),
    )

    assert runs_create.await_count == 1


@pytest.mark.asyncio
async def test_process_slack_mention_drops_retried_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second delivery of the same event_ts after the first completes is dropped."""
    webapp._slack_thread_locks.clear()
    webapp._slack_processed_events.clear()

    webapp._mark_slack_event_processed("C1", "1.0", "1.5")

    runs_create = AsyncMock()
    fake_client = MagicMock()
    fake_client.runs.create = runs_create
    monkeypatch.setattr(webapp, "get_client", lambda url: fake_client)

    async def _set_status(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(webapp, "set_slack_assistant_status", _set_status)

    await webapp.process_slack_mention(
        {
            "channel_id": "C1",
            "thread_ts": "1.0",
            "event_ts": "1.5",
            "user_id": "U",
            "text": "x",
            "bot_user_id": "B",
        },
        {"owner": "o", "name": "n"},
    )

    assert runs_create.await_count == 0
