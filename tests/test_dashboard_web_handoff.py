from __future__ import annotations

from typing import Any

import pytest

from agent.dashboard import thread_api


class _FakeThreads:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.metadata = metadata
        self.updates: list[dict[str, Any]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self.metadata}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append(metadata)
        self.metadata.update(metadata)


class _FakeRuns:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        self.created.append({"args": args, "kwargs": kwargs})
        return {"run_id": "run-1"}


class _FakeClient:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self.threads = _FakeThreads(metadata)
        self.runs = _FakeRuns()


async def _inactive_thread(thread_id: str) -> bool:
    return False


async def _active_thread(thread_id: str) -> bool:
    return True


async def _noop_token_check(login: str) -> None:
    return None


async def _empty_profile(login: str) -> dict[str, Any]:
    return {}


async def _run_email(login: str, profile: dict[str, Any]) -> str:
    return "octocat@example.com"


@pytest.mark.asyncio
async def test_dashboard_followup_on_slack_thread_uses_dashboard_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "slack",
        "github_login": "octocat",
        "triggering_user_email": "octocat@example.com",
        "repo_owner": "octo",
        "repo_name": "repo",
        "source_context": {
            "slack_thread": {"channel_id": "C1", "thread_ts": "123.45"},
        },
    }
    client = _FakeClient(metadata)

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "is_thread_active", _inactive_thread)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", _noop_token_check)
    monkeypatch.setattr(thread_api, "get_profile", _empty_profile)
    monkeypatch.setattr(thread_api, "_resolve_run_email", _run_email)

    await thread_api.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_api.ThreadMessageBody(content="continue in web"),
        email="octocat@example.com",
    )

    run_config = client.runs.created[0]["kwargs"]["config"]["configurable"]
    assert client.threads.updates[0]["source"] == "dashboard"
    assert run_config["source"] == "dashboard"
    assert "slack_thread" not in run_config
    assert run_config["repo"] == {"owner": "octo", "name": "repo"}


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_thread_queues_dashboard_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "slack",
        "github_login": "octocat",
        "triggering_user_email": "octocat@example.com",
    }
    client = _FakeClient(metadata)
    queued_messages: list[object] = []

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        queued_messages.append(message_content)
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "is_thread_active", _active_thread)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue_message_for_thread)

    await thread_api.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_api.ThreadMessageBody(content="continue in web"),
        email="octocat@example.com",
    )

    assert client.threads.updates[0]["source"] == "dashboard"
    assert queued_messages == [{"text": "continue in web", "source": "dashboard"}]
