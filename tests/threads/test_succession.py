import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import JsonValue

from openswe.thread_ids import mda_successor_thread_id
from openswe.threads import index, succession

THREAD = "00000000-0000-4000-8000-000000000001"


class _Threads:
    def __init__(self) -> None:
        self.created: list[tuple[str, Mapping[str, JsonValue]]] = []
        self.updated: list[tuple[str, Mapping[str, JsonValue]]] = []

    async def create(
        self, thread_id: str, *, title: str, metadata: Mapping[str, JsonValue]
    ) -> None:
        self.created.append((thread_id, metadata))
        await index.create(thread_id, runtime="mda", metadata=metadata, if_exists="do_nothing")

    async def update(self, thread_id: str, *, metadata: Mapping[str, JsonValue]) -> None:
        self.updated.append((thread_id, metadata))


@pytest.mark.usefixtures("registry_db")
async def test_work_on_a_local_thread_moves_once_to_a_successor_with_its_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Threads()
    monkeypatch.setattr(succession, "THREADS", store)
    await index.create(
        THREAD,
        runtime="local",
        metadata={"kind": "reviewer", "watch": True, "latest_run_id": "run-1"},
    )

    first = await succession.succeed(THREAD)
    second = await succession.succeed(THREAD)

    successor = mda_successor_thread_id(THREAD)
    assert first == second == successor
    [(created_id, metadata)] = store.created
    assert created_id == successor
    assert metadata == {"kind": "reviewer", "watch": True}
    assert store.updated == [(THREAD, {"succeeded_by": successor})]
    assert await index.current_thread_id(THREAD) == successor


class _MemoryThreads(_Threads):
    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, index.IndexedThread] = {}

    async def get(self, thread_id: str) -> index.IndexedThread | None:
        return self.rows.get(thread_id)

    async def create(
        self, thread_id: str, *, title: str, metadata: Mapping[str, JsonValue]
    ) -> None:
        await asyncio.sleep(0)
        self.rows.setdefault(
            thread_id,
            index.IndexedThread(
                thread_id=thread_id,
                runtime="mda",
                metadata=dict(metadata),
                status="idle",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
        )

    async def update(self, thread_id: str, *, metadata: Mapping[str, JsonValue]) -> None:
        self.rows[thread_id].metadata.update(metadata)


class TestDeletedReviewerRecovery:
    @pytest.fixture(autouse=True)
    async def setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.store = _MemoryThreads()
        monkeypatch.setattr(succession, "THREADS", self.store)
        monkeypatch.setattr(index, "get", self.store.get)
        await self.store.create(
            thread_id=THREAD,
            title="Review",
            metadata={
                "kind": "reviewer",
                "watch": True,
                "sandbox_id": "deleted-box",
                "sandbox_owner": "mda",
                "sandbox_work_dir": "/old-workspace",
                "latest_run_id": "old-run",
                "runtime": "mda",
                "running_background_tasks": ["old-task"],
                "owner_type": "system",
                "visibility": "private",
                "github_token_repositories": ["langchain-ai/langchainplus"],
            },
        )

    async def test_restarts_once_under_concurrent_requests_and_follows_successors(self) -> None:
        with patch(
            "openswe.sandboxes.providers.langsmith.sandbox_exists",
            new=AsyncMock(return_value=False),
        ):
            first, second = await asyncio.gather(
                succession.succeed(THREAD, restart_deleted=True),
                succession.succeed(THREAD, restart_deleted=True),
            )
            assert first == second == mda_successor_thread_id(THREAD)
            assert await succession.succeed(THREAD, restart_deleted=True) == first
            assert await index.current_thread_id(THREAD) == first
            assert self.store.rows[first].metadata == {
                "kind": "reviewer",
                "watch": True,
                "owner_type": "system",
                "visibility": "private",
                "github_token_repositories": ["langchain-ai/langchainplus"],
            }
            self.store.rows[first].metadata.update(sandbox_id="also-deleted", sandbox_owner="mda")
            third = await succession.succeed(THREAD, restart_deleted=True)
            assert third == mda_successor_thread_id(first)
            assert await index.current_thread_id(THREAD) == third
        assert len(self.store.rows) == 3
        assert self.store.rows[THREAD].metadata["sandbox_id"] == "deleted-box"

    @pytest.mark.parametrize("exists", [True, RuntimeError("provider unavailable")])
    async def test_does_not_restart_without_confirmed_deletion(
        self, exists: bool | Exception
    ) -> None:
        probe = (
            AsyncMock(side_effect=exists)
            if isinstance(exists, Exception)
            else AsyncMock(return_value=exists)
        )
        with patch("openswe.sandboxes.providers.langsmith.sandbox_exists", new=probe):
            assert await succession.succeed(THREAD, restart_deleted=True) == THREAD
        assert len(self.store.rows) == 1

    @pytest.mark.parametrize("kind,restart", [("agent", True), ("reviewer", False)])
    async def test_ordinary_threads_and_non_restart_calls_are_unchanged(
        self, kind: str, restart: bool
    ) -> None:
        self.store.rows[THREAD].metadata["kind"] = kind
        assert await succession.succeed(THREAD, restart_deleted=restart) == THREAD
        assert len(self.store.rows) == 1

    async def test_recovers_if_successor_creation_succeeds_but_pointer_publication_fails(
        self,
    ) -> None:
        with (
            patch(
                "openswe.sandboxes.providers.langsmith.sandbox_exists",
                new=AsyncMock(return_value=False),
            ),
            patch.object(self.store, "update", side_effect=RuntimeError("lost update")),
            pytest.raises(RuntimeError, match="lost update"),
        ):
            await succession.succeed(THREAD, restart_deleted=True)
        with patch(
            "openswe.sandboxes.providers.langsmith.sandbox_exists",
            new=AsyncMock(return_value=False),
        ):
            successor = await succession.succeed(THREAD, restart_deleted=True)
        assert await index.current_thread_id(THREAD) == successor
        assert len(self.store.rows) == 2

    async def test_ignores_unrelated_successor_pointer(self) -> None:
        self.store.rows[THREAD].metadata["succeeded_by"] = "another-users-thread"
        assert await index.current_thread_id(THREAD) == THREAD
