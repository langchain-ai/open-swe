from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

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
    monkeypatch.setattr(thread_api, "get_thread_active_status", _inactive_thread)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", _noop_token_check)
    monkeypatch.setattr(thread_api, "get_profile", _empty_profile)
    monkeypatch.setattr(thread_api, "_resolve_run_email", _run_email)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_api.ThreadMessageBody(content="continue in web"),
            email="octocat@example.com",
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_dashboard_followup_sends_image_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
        "repo_owner": "octo",
        "repo_name": "repo",
    }
    client = _FakeClient(metadata)

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _inactive_thread)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", _noop_token_check)
    monkeypatch.setattr(thread_api, "get_profile", _empty_profile)
    monkeypatch.setattr(thread_api, "_resolve_run_email", _run_email)
    monkeypatch.setattr(
        thread_api,
        "create_image_block",
        lambda *, base64, mime_type: {"type": "image", "data": base64, "mime_type": mime_type},
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_api.ThreadMessageBody(
                content="describe this",
                images=[
                    thread_api.DashboardImageBody(
                        base64="aW1hZ2U=",
                        mimeType="image/png",
                        fileName="screenshot.png",
                    )
                ],
            ),
        )

    assert exc_info.value.status_code == 409


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
    monkeypatch.setattr(thread_api, "get_thread_active_status", _active_thread)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue_message_for_thread)

    await thread_api.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_api.ThreadMessageBody(content="continue in web"),
        email="octocat@example.com",
    )

    assert client.threads.updates[0]["source"] == "dashboard"
    assert queued_messages == [{"text": "continue in web", "source": "dashboard"}]


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_thread_queues_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
        "resolved_model": "openai:gpt-5.5",
    }
    client = _FakeClient(metadata)
    queued_messages: list[object] = []

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        queued_messages.append(message_content)
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _active_thread)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue_message_for_thread)
    monkeypatch.setattr(
        thread_api,
        "create_image_block",
        lambda *, base64, mime_type: {"type": "image", "data": base64, "mime_type": mime_type},
    )

    await thread_api.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_api.ThreadMessageBody(
            content="continue in web",
            images=[thread_api.DashboardImageBody(base64="aW1hZ2U=", mimeType="image/png")],
        ),
    )

    assert queued_messages == [
        {
            "text": "continue in web",
            "source": "dashboard",
            "images": [{"type": "image", "data": "aW1hZ2U=", "mime_type": "image/png"}],
        }
    ]


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_text_only_thread_rejects_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
        "resolved_model": "fireworks:accounts/fireworks/models/deepseek-v4-pro",
    }
    client = _FakeClient(metadata)
    queued_messages: list[object] = []

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        queued_messages.append(message_content)
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _active_thread)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue_message_for_thread)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_api.ThreadMessageBody(
                content="continue in web",
                images=[thread_api.DashboardImageBody(base64="aW1hZ2U=", mimeType="image/png")],
                model_id="openai:gpt-5.5",
                effort="medium",
            ),
        )

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail
    assert queued_messages == []
    assert client.threads.updates == []


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_unknown_model_rejects_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
    }
    client = _FakeClient(metadata)

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _active_thread)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_api.ThreadMessageBody(
                content="continue in web",
                images=[thread_api.DashboardImageBody(base64="aW1hZ2U=", mimeType="image/png")],
            ),
        )

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail
    assert client.threads.updates == []


@pytest.mark.asyncio
async def test_dashboard_followup_preserves_explicit_repo_less_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
        "repo_explicitly_none": True,
    }
    client = _FakeClient(metadata)

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _inactive_thread)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", _noop_token_check)
    monkeypatch.setattr(thread_api, "get_profile", _empty_profile)
    monkeypatch.setattr(thread_api, "_resolve_run_email", _run_email)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_api.ThreadMessageBody(content="continue in web"),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_dashboard_followup_without_repo_metadata_allows_team_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
    }
    client = _FakeClient(metadata)

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _inactive_thread)
    monkeypatch.setattr(thread_api, "_ensure_dashboard_github_token", _noop_token_check)
    monkeypatch.setattr(thread_api, "get_profile", _empty_profile)
    monkeypatch.setattr(thread_api, "_resolve_run_email", _run_email)

    with pytest.raises(HTTPException) as exc_info:
        await thread_api.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_api.ThreadMessageBody(content="continue in web"),
        )

    assert exc_info.value.status_code == 409
