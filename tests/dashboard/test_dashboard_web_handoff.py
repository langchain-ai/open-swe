import uuid
from typing import Any, cast

import pytest
from fastapi import HTTPException

from openswe.threads import handlers
from openswe.threads import runs as thread_runs
from tests.conftest import patch_thread_module


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


def _queued_message_without_metadata(
    queued_messages: list[object], expected_queue_id: str | None = None
) -> dict[str, object]:
    assert len(queued_messages) == 1
    queued_message = cast(dict[str, object], queued_messages[0])
    queue_id = cast(str, queued_message.pop("queue_id"))
    if expected_queue_id:
        assert queue_id == expected_queue_id
    else:
        assert queue_id.startswith("queued-")
    assert isinstance(queued_message.pop("created_at_ms"), int)
    return queued_message


async def _noop_token_check(login: str) -> None:
    return None


async def _empty_profile(login: str) -> dict[str, Any]:
    return {}


async def _run_email(login: str, profile: dict[str, Any]) -> str:
    return "octocat@example.com"


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

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_thread_active_status", _active_thread)
    patch_thread_module(monkeypatch, "queue_message_for_thread", fake_queue_message_for_thread)

    client_message_id = uuid.UUID("8a60896d-65ca-4e40-8a2d-1fbe81777001")
    await handlers.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_runs.ThreadMessageBody(
            content="continue in web", client_message_id=client_message_id
        ),
        email="octocat@example.com",
    )

    assert client.threads.updates[0]["source"] == "dashboard"
    assert _queued_message_without_metadata(queued_messages, str(client_message_id)) == {
        "text": "continue in web",
        "source": "dashboard",
        "surface": "web",
        "sender": {
            "id": "github:octocat",
            "platform": "github",
            "github_login": "octocat",
            "email": "octocat@example.com",
        },
    }


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_thread_queues_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
        "resolved_model": "openai:gpt-6.1-sol",
    }
    client = _FakeClient(metadata)
    queued_messages: list[object] = []

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        queued_messages.append(message_content)
        return True

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_thread_active_status", _active_thread)
    patch_thread_module(monkeypatch, "queue_message_for_thread", fake_queue_message_for_thread)
    patch_thread_module(
        monkeypatch,
        "create_image_block",
        lambda *, base64, mime_type: {"type": "image", "data": base64, "mime_type": mime_type},
    )

    await handlers.send_dashboard_message(
        "thread-1",
        "octocat",
        thread_runs.ThreadMessageBody(
            content="continue in web",
            images=[thread_runs.DashboardImageBody(base64="aW1hZ2U=", mimeType="image/png")],
        ),
    )

    assert _queued_message_without_metadata(queued_messages) == {
        "text": "continue in web",
        "source": "dashboard",
        "surface": "web",
        "sender": {
            "id": "github:octocat",
            "platform": "github",
            "github_login": "octocat",
        },
        "images": [{"type": "image", "data": "aW1hZ2U=", "mime_type": "image/png"}],
    }


@pytest.mark.asyncio
async def test_dashboard_followup_on_busy_text_only_thread_rejects_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = {
        "source": "dashboard",
        "github_login": "octocat",
        "resolved_model": "fireworks:accounts/fireworks/models/kimi-k3",
    }
    client = _FakeClient(metadata)
    queued_messages: list[object] = []

    async def fake_queue_message_for_thread(thread_id: str, message_content: object) -> bool:
        queued_messages.append(message_content)
        return True

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_thread_active_status", _active_thread)
    patch_thread_module(monkeypatch, "queue_message_for_thread", fake_queue_message_for_thread)

    with pytest.raises(HTTPException) as exc_info:
        await handlers.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_runs.ThreadMessageBody(
                content="continue in web",
                images=[thread_runs.DashboardImageBody(base64="aW1hZ2U=", mimeType="image/png")],
                model_id="openai:gpt-6.1-sol",
                effort="medium",
            ),
        )

    assert exc_info.value.status_code == 422
    assert "does not support image input" in exc_info.value.detail
    assert queued_messages == []
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

    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_thread_active_status", _inactive_thread)
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", _noop_token_check)
    patch_thread_module(monkeypatch, "get_profile", _empty_profile)
    patch_thread_module(monkeypatch, "resolve_run_email", _run_email)

    with pytest.raises(HTTPException) as exc_info:
        await handlers.send_dashboard_message(
            "thread-1",
            "octocat",
            thread_runs.ThreadMessageBody(content="continue in web"),
        )

    assert exc_info.value.status_code == 409
