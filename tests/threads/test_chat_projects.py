from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid7

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import text

from openswe.chat_projects.models import LocalFolder, ProjectBody
from openswe.chat_projects.routes import router
from openswe.chat_projects.store import ChatProjects
from openswe.dashboard.oauth import require_session
from openswe.database.postgres import transaction
from openswe.thread_ids import mda_successor_thread_id
from openswe.threads import index
from openswe.tools import access as tool_access
from openswe.tools import chat_projects as project_tools
from openswe.tools.access import Access

pytestmark = pytest.mark.usefixtures("registry_db")


class ProjectOwner:
    def __init__(self) -> None:
        self.id = uuid7()
        self.store = ChatProjects(self.id, f"user:{self.id}")

    async def seed(self) -> None:
        async with transaction() as conn:
            await conn.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": self.id})
            await conn.execute(
                text(
                    "INSERT INTO user_identity (provider, external_id, user_id) "
                    "VALUES ('langsmith', :external_id, :id)"
                ),
                {"id": self.id, "external_id": str(self.id)},
            )

    async def thread(
        self, thread_id: str, *, runtime: index.Runtime = "mda"
    ) -> index.IndexedThread:
        return await index.create(
            thread_id,
            runtime=runtime,
            metadata={
                "source": "dashboard",
                "visibility": "private",
                "owner_login": self.store.subject,
                "latest_run_status": "success",
            },
        )

    async def session(self) -> dict[str, str]:
        return {
            "sub": f"user:{self.id}",
            "user_id": str(self.id),
            "provider": "langsmith",
            "langsmith_subject": str(self.id),
        }

    async def client(self) -> AsyncIterator[httpx.AsyncClient]:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_session] = self.session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def test_thread_updates_and_deletion_invalidate_owner_project_lists() -> None:
    alice = ProjectOwner()
    await alice.seed()
    thread = await alice.thread("project-chat")
    project = await alice.store.create(ProjectBody(name="Work"))
    await alice.store.assign(thread.thread_id, project.id)
    async with transaction() as conn:
        await conn.execute(text("DELETE FROM ui_invalidation"))
    await index.update(thread.thread_id, {"title": "Changed elsewhere"})
    async with transaction() as conn:
        topics = (await conn.execute(text("SELECT topic FROM ui_invalidation"))).scalars().all()
        assert f"chat-projects/{alice.id}" in topics
        await conn.execute(text("DELETE FROM ui_invalidation"))
    await index.delete(thread.thread_id)
    async with transaction() as conn:
        topics = (await conn.execute(text("SELECT topic FROM ui_invalidation"))).scalars().all()
        assert f"chat-projects/{alice.id}" in topics
    assert (await alice.store.collection()).memberships == {}


async def test_project_crud_is_personal_and_deletion_never_deletes_chats() -> None:
    alice, bob = ProjectOwner(), ProjectOwner()
    await alice.seed()
    await bob.seed()
    thread = await alice.thread("alice-chat")
    project = await alice.store.create(ProjectBody(name="  Work  ", repo="langchain-ai/open-swe"))
    assert project.name == "Work"
    await alice.store.assign(thread.thread_id, project.id)
    assert (await bob.store.collection()).model_dump() == {"projects": [], "memberships": {}}

    for operation in (
        bob.store.get(project.id),
        bob.store.update(project.id, ProjectBody(name="Stolen")),
        bob.store.delete(project.id),
        bob.store.assign(thread.thread_id, project.id),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code in {403, 404}

    changed = await alice.store.update(
        project.id,
        ProjectBody(
            name="Local", localFolder=LocalFolder(path="/home/alice/app", deviceId="device")
        ),
    )
    assert changed.repo is None
    assert changed.localFolder is not None
    async with transaction() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM chat_project WHERE id = :id"), {"id": project.id}
                )
            )
            .mappings()
            .one()
        )
        assert row["created_by"] == alice.id == row["updated_by"]
        assert row["updated_at"] >= row["created_at"]
    await alice.store.delete(project.id)
    assert (await alice.store.collection()).memberships == {}
    assert await index.get(thread.thread_id) == thread
    async with transaction() as conn:
        invalidations = (
            (
                await conn.execute(
                    text("SELECT topic FROM ui_invalidation WHERE topic LIKE 'chat-projects/%'")
                )
            )
            .scalars()
            .all()
        )
    assert invalidations == [f"chat-projects/{alice.id}"] * 4


async def test_memberships_are_personal_exclusive_and_rechecked_when_access_changes() -> None:
    alice, bob = ProjectOwner(), ProjectOwner()
    await alice.seed()
    await bob.seed()
    alice.store.subject, bob.store.subject = "alice", "bob"
    thread = await alice.thread("chat")
    await index.update(thread.thread_id, {"visibility": "public"})
    first = await alice.store.create(ProjectBody(name="First"))
    second = await alice.store.create(ProjectBody(name="Second"))
    other = await bob.store.create(ProjectBody(name="Bob's"))
    await alice.store.assign("chat", first.id)
    await bob.store.assign("chat", other.id)
    await alice.store.assign("chat", second.id)
    assert (await alice.store.collection()).memberships == {"chat": str(second.id)}
    assert (await bob.store.collection()).memberships == {"chat": str(other.id)}
    with pytest.raises(HTTPException) as error:
        await alice.store.assign("chat", other.id)
    assert error.value.status_code == 404
    assert (await alice.store.collection()).memberships == {"chat": str(second.id)}

    await index.update("chat", {"visibility": "private"})
    assert (await bob.store.collection()).memberships == {}
    assert (await bob.store.threads(other.id)).items == []
    with pytest.raises(HTTPException):
        await bob.store.assign("chat", None)
    await alice.store.assign("chat", None)
    assert (await alice.store.collection()).memberships == {}


async def test_successor_membership_moves_and_clears_without_a_stale_predecessor() -> None:
    owner = ProjectOwner()
    await owner.seed()
    first = await owner.store.create(ProjectBody(name="First"))
    second = await owner.store.create(ProjectBody(name="Second"))
    await owner.thread("old-chat", runtime="local")
    await owner.store.assign("old-chat", first.id)
    successor_id = mda_successor_thread_id("old-chat")
    await owner.thread(successor_id)
    assert (await owner.store.collection()).memberships == {successor_id: str(first.id)}
    await owner.store.assign(successor_id, second.id)
    assert (await owner.store.collection()).memberships == {successor_id: str(second.id)}
    assert (await owner.store.threads(first.id)).items == []
    await owner.store.assign("old-chat", None)
    assert (await owner.store.collection()).memberships == {}


async def test_project_pagination_filters_access_unlisted_and_archived_before_slicing() -> None:
    owner, other = ProjectOwner(), ProjectOwner()
    await owner.seed()
    await other.seed()
    project = await owner.store.create(ProjectBody(name="Work"))
    for number in range(6):
        thread_id = f"chat-{number}"
        await owner.thread(thread_id)
        await owner.store.assign(thread_id, project.id)
    await index.update("chat-5", {"owner_login": f"user:{other.id}"})
    await index.update("chat-4", {"unlisted": True})
    await index.update("chat-3", {"resolved": True})
    async with transaction() as conn:
        for number in range(6):
            await conn.execute(
                text(
                    "UPDATE thread_index SET updated_at = :updated_at WHERE thread_id = :thread_id"
                ),
                {
                    "thread_id": f"chat-{number}",
                    "updated_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=number),
                },
            )
    page = await owner.store.threads(project.id, limit=2)
    assert [item["id"] for item in page.items] == ["chat-2", "chat-1"]
    assert page.hasMore
    page = await owner.store.threads(project.id, limit=2, offset=2)
    assert [item["id"] for item in page.items] == ["chat-0"]
    assert not page.hasMore
    page = await owner.store.threads(project.id, resolved=True)
    assert [item["id"] for item in page.items] == ["chat-3"]


async def test_api_uses_canonical_session_user_and_rejects_invalid_context() -> None:
    owner = ProjectOwner()
    await owner.seed()
    await owner.thread("chat")
    async for client in owner.client():
        response = await client.post(
            "/chat-projects", json={"name": "  Work ", "repo": None, "localFolder": None}
        )
        assert response.status_code == 201, response.text
        project_id = response.json()["id"]
        assert UUID(project_id).version == 7
        assert response.json() == {
            "id": project_id,
            "name": "Work",
            "repo": None,
            "localFolder": None,
        }
        assigned = await client.put("/chat-projects/threads/chat", json={"projectId": project_id})
        assert assigned.status_code == 204, assigned.text
        response = await client.get("/chat-projects")
        assert response.status_code == 200
        assert response.json()["memberships"] == {"chat": project_id}
        invalid = await client.patch(f"/chat-projects/{project_id}", json={"name": " "})
        assert invalid.status_code == 422
        deleted = await client.delete(f"/chat-projects/{project_id}")
        assert deleted.status_code == 204, deleted.text
        assert await index.get("chat") is not None


async def test_project_tools_gate_personal_reads_and_redact_sole_writer_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = ProjectOwner()
    await owner.seed()
    config = {
        "configurable": {
            "auth_provider": "langsmith",
            "user_id": str(owner.id),
            "sender_id": f"user:{owner.id}",
            "source": "dashboard",
        }
    }
    monkeypatch.setattr(project_tools, "get_config", lambda: config)
    gate = AsyncMock(return_value=Access(private=True, owner=True, direct=True))
    monkeypatch.setattr(tool_access, "resolve_access", gate)
    created = await project_tools.create_chat_project("Private work", repo="org/repo")
    assert created["ok"] is True
    projects = await project_tools.list_chat_projects()
    assert projects["ok"] is True
    assert len((await owner.store.collection()).projects) == 1

    gate.return_value = Access(sole=True, direct=True)
    denied = await project_tools.list_chat_projects()
    assert denied["ok"] is False
    assert "projects" not in denied
    redacted = await project_tools.create_chat_project("Sole writer")
    assert redacted == {"ok": True}
    assert len((await owner.store.collection()).projects) == 2

    gate.return_value = Access(private=True, owner=True, direct=False)
    assert (await project_tools.create_chat_project("Automatic"))["ok"] is False
    assert len((await owner.store.collection()).projects) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Work", "localFolder": {"path": "relative/path", "deviceId": "device"}},
        {"name": "Work", "localFolder": {"path": "/tmp/\x00bad", "deviceId": "device"}},
        {"name": "Work", "repo": "https://github.com/org/repo"},
        {"name": "Work", "repo": "org/repo", "localFolder": {"path": "/app", "deviceId": "device"}},
    ],
)
async def test_context_cannot_smuggle_a_url_relative_path_or_both_contexts(
    body: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ProjectBody.model_validate(body)
