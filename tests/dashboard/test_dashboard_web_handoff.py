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


class _FakeStore:
    def __init__(
        self, items: dict[tuple[tuple[str, ...], str], dict[str, Any]] | None = None
    ) -> None:
        self.items = items or {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))


class _FakeClient:
    def __init__(
        self,
        metadata: dict[str, Any],
        store_items: dict[tuple[tuple[str, ...], str], dict[str, Any]] | None = None,
    ) -> None:
        self.threads = _FakeThreads(metadata)
        self.runs = _FakeRuns()
        self.store = _FakeStore(store_items)


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
    assert queued_messages == [
        {"text": "continue in web", "source": "dashboard", "from_owner": True}
    ]


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_slack_thread_updates_trace_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "slack",
        "github_login": "octocat",
        "triggering_user_email": "octocat@example.com",
        "source_context": {
            "slack_thread": {
                "channel_id": "C1",
                "thread_ts": "123.45",
                "trace_message_ts": "123.46",
            }
        },
    }
    client = _FakeClient(metadata)
    queued_messages: list[object] = []
    handoff_updates: list[dict[str, str]] = []

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        queued_messages.append(message_content)
        return True

    async def fake_update_trace_reply(channel_id: str, message_ts: str, thread_id: str) -> bool:
        handoff_updates.append(
            {"channel_id": channel_id, "message_ts": message_ts, "thread_id": thread_id}
        )
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _active_thread)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue_message_for_thread)
    monkeypatch.setattr(
        thread_api, "update_slack_trace_reply_for_web_handoff", fake_update_trace_reply
    )

    await thread_api.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_api.ThreadMessageBody(content="continue in web"),
        email="octocat@example.com",
    )

    assert queued_messages == [
        {"text": "continue in web", "source": "dashboard", "from_owner": True}
    ]
    assert handoff_updates == [
        {"channel_id": "C1", "message_ts": "123.46", "thread_id": "thread-1"}
    ]


@pytest.mark.asyncio
async def test_dashboard_followup_uses_stored_trace_reply_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "slack",
        "github_login": "octocat",
        "triggering_user_email": "octocat@example.com",
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "123.45"}},
    }
    client = _FakeClient(
        metadata,
        {
            (("slack_run_map", "C1"), "thread:123.45"): {
                "value": {"run_id": "run-1", "thread_ts": "123.45", "trace_message_ts": "123.46"}
            }
        },
    )
    handoff_updates: list[dict[str, str]] = []

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        return True

    async def fake_update_trace_reply(channel_id: str, message_ts: str, thread_id: str) -> bool:
        handoff_updates.append(
            {"channel_id": channel_id, "message_ts": message_ts, "thread_id": thread_id}
        )
        return True

    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "get_thread_active_status", _active_thread)
    monkeypatch.setattr(thread_api, "queue_message_for_thread", fake_queue_message_for_thread)
    monkeypatch.setattr(
        thread_api, "update_slack_trace_reply_for_web_handoff", fake_update_trace_reply
    )

    await thread_api.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_api.ThreadMessageBody(content="continue in web"),
        email="octocat@example.com",
    )

    assert handoff_updates == [
        {"channel_id": "C1", "message_ts": "123.46", "thread_id": "thread-1"}
    ]


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_thread_queues_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
        "resolved_model": "openai:gpt-5.6-sol",
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
            "from_owner": True,
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
                model_id="openai:gpt-5.6-sol",
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
