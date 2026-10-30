"""Short links to sandbox files, against a real migrated schema."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from langsmith.sandbox import ResourceNotFoundError
from sqlalchemy import text

from openswe.database import postgres
from openswe.sandboxes import file_link_routes
from openswe.sandboxes.file_links import LINKS_PATH, SandboxFileLink, create_link, get_link
from openswe.sandboxes.providers import langsmith as langsmith_provider
from openswe.transcript.engine import Command, append, delete_transcript
from openswe.transcript.events import ThreadCreated


async def _create_thread(thread_id: str) -> None:
    created = ThreadCreated(title="A thread", source="dashboard", owner_login="test-user")
    await append(
        thread_id,
        [Command(command_id=f"thread:{thread_id}:created", event=created, actor_kind="user")],
    )


async def _link(
    thread_id: str = "thread-1", expires_in_seconds: int | None = None
) -> SandboxFileLink | None:
    return await create_link(
        thread_id=thread_id,
        sandbox_id="sandbox-1",
        path="/workspace/project/shot.png",
        content_type="image/png",
        content_disposition="inline",
        expires_in_seconds=expires_in_seconds,
    )


async def test_a_link_stores_its_target_under_a_short_random_id(registry_db: None) -> None:
    await _create_thread("thread-1")

    link = await _link()

    assert link is not None
    assert len(link.id) == 22
    stored = await get_link(link.id)
    assert stored is not None
    assert (
        stored.thread_id,
        stored.sandbox_id,
        stored.path,
        stored.content_type,
        stored.content_disposition,
        stored.expires_at,
    ) == ("thread-1", "sandbox-1", "/workspace/project/shot.png", "image/png", "inline", None)


async def test_a_link_records_when_it_expires(registry_db: None) -> None:
    await _create_thread("thread-1")
    before = datetime.now(UTC)

    link = await _link(expires_in_seconds=600)

    assert link is not None
    stored = await get_link(link.id)
    assert stored is not None and stored.expires_at is not None
    assert (
        before + timedelta(seconds=600)
        <= stored.expires_at
        <= datetime.now(UTC) + timedelta(seconds=600)
    )


async def test_a_thread_without_a_transcript_row_gets_no_link(registry_db: None) -> None:
    assert await _link("legacy-thread") is None


async def test_deleting_the_thread_deletes_its_links(registry_db: None) -> None:
    await _create_thread("thread-1")
    link = await _link()
    assert link is not None

    assert await delete_transcript("thread-1")

    assert await get_link(link.id) is None


async def test_an_unknown_id_has_no_link(registry_db: None) -> None:
    assert await get_link("no-such-link") is None


class _SandboxClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __aenter__(self) -> _SandboxClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def generate_download_url(self, name: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((name, path, kwargs))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            download_url="https://sandbox.example/download?token=fresh",
            token="fresh",
            expires_at=None,
        )


def _fake_sandbox(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None = None
) -> _SandboxClient:
    client = _SandboxClient(error)
    monkeypatch.setattr(langsmith_provider, "get_async_sandbox_client", lambda: client)
    return client


async def _open(link_id: str) -> httpx.Response:
    from openswe.api.app import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://openswe.example"
    ) as http:
        return await http.get(f"{LINKS_PATH}/{link_id}")


async def test_opening_a_link_redirects_to_a_freshly_signed_url(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _create_thread("thread-1")
    link = await _link()
    assert link is not None
    sandbox = _fake_sandbox(monkeypatch)

    response = await _open(link.id)

    assert response.status_code == 302
    assert response.headers["location"] == "https://sandbox.example/download?token=fresh"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert sandbox.calls == [
        (
            "sandbox-1",
            "/workspace/project/shot.png",
            {
                "expires_in_seconds": file_link_routes.SIGNED_URL_SECONDS,
                "content_type": "image/png",
                "content_disposition": "inline",
            },
        )
    ]


async def test_a_signed_url_never_outlives_its_link(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _create_thread("thread-1")
    link = await _link(expires_in_seconds=600)
    assert link is not None
    sandbox = _fake_sandbox(monkeypatch)

    response = await _open(link.id)

    assert response.status_code == 302
    assert 590 <= sandbox.calls[0][2]["expires_in_seconds"] <= 600


async def test_an_unknown_link_is_not_found(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _fake_sandbox(monkeypatch)

    response = await _open("no-such-link")

    assert response.status_code == 404
    assert sandbox.calls == []


async def test_an_expired_link_is_gone(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    await _create_thread("thread-1")
    link = await _link(expires_in_seconds=600)
    assert link is not None
    async with postgres.transaction() as conn:
        await conn.execute(
            text(
                "UPDATE sandbox_file_link SET expires_at = clock_timestamp() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": link.id},
        )
    sandbox = _fake_sandbox(monkeypatch)

    response = await _open(link.id)

    assert response.status_code == 410
    assert sandbox.calls == []


async def test_a_link_to_a_deleted_sandbox_is_gone(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _create_thread("thread-1")
    link = await _link()
    assert link is not None
    _fake_sandbox(
        monkeypatch,
        ResourceNotFoundError("Sandbox 'sandbox-1' not found", resource_type="sandbox"),
    )

    response = await _open(link.id)

    assert response.status_code == 410
