import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import PermissionDeniedError

from agent.utils import thread_pr_state

type LockServer = tuple[LangGraphClient, set[str], list[httpx.Request]]


@pytest.fixture
async def lock_server() -> AsyncIterator[LockServer]:
    held: set[str] = set()
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            lock_id = json.loads(request.content)["thread_id"]
            if lock_id in held:
                return httpx.Response(409, json={"detail": "exists"})
            held.add(lock_id)
            return httpx.Response(200, json={"thread_id": lock_id})
        held.remove(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(204)

    async with httpx.AsyncClient(
        base_url="http://langgraph.test", transport=httpx.MockTransport(handle)
    ) as http:
        yield LangGraphClient(http), held, requests


async def test_local_contenders_do_not_poll_the_distributed_lock(lock_server: LockServer) -> None:
    client, held, requests = lock_server
    entered = asyncio.Event()
    release = asyncio.Event()

    async def owner() -> None:
        async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
            entered.set()
            await release.wait()

    completed = 0

    async def contender() -> None:
        nonlocal completed
        async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
            completed += 1

    owner_task = asyncio.create_task(owner())
    await entered.wait()
    contenders = [asyncio.create_task(contender()) for _ in range(10)]
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(owner_task, *contenders)

    assert completed == 10
    assert held == set()
    assert len([request for request in requests if request.method == "POST"]) == 11


async def test_different_threads_can_hold_locks_at_the_same_time(lock_server: LockServer) -> None:
    client, held, _ = lock_server
    async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
        async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-2"):
            assert len(held) == 2
    assert held == set()


async def test_conflicts_back_off_until_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    elapsed = 0.0
    sleeps: list[float] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        assert request.method == "POST"
        attempts += 1
        return httpx.Response(409, json={"detail": "exists"})

    async def sleep(delay: float) -> None:
        nonlocal elapsed
        sleeps.append(delay)
        elapsed += delay

    monkeypatch.setattr(asyncio.get_running_loop(), "time", lambda: elapsed)
    monkeypatch.setattr(thread_pr_state.asyncio, "sleep", sleep)
    monkeypatch.setattr(thread_pr_state.random, "uniform", lambda low, high: (low + high) / 2)
    async with httpx.AsyncClient(
        base_url="http://langgraph.test", transport=httpx.MockTransport(handle)
    ) as http:
        with pytest.raises(TimeoutError, match="thread-1"):
            async with thread_pr_state.agent_thread_pr_state_lock(
                LangGraphClient(http), "thread-1"
            ):
                pytest.fail("a contended lock was acquired")

    assert attempts < 65
    assert elapsed == pytest.approx(60)
    assert all(0 < delay <= 2 for delay in sleeps)


async def test_non_conflict_failure_is_not_retried() -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403, json={"detail": "forbidden"})

    async with httpx.AsyncClient(
        base_url="http://langgraph.test", transport=httpx.MockTransport(handle)
    ) as http:
        with pytest.raises(PermissionDeniedError):
            async with thread_pr_state.agent_thread_pr_state_lock(
                LangGraphClient(http), "thread-1"
            ):
                pytest.fail("an unauthorized lock was acquired")
    assert len(requests) == 1


async def test_cancelled_waiter_does_not_release_the_owner_lock(lock_server: LockServer) -> None:
    client, held, requests = lock_server

    async def contender() -> None:
        async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
            pytest.fail("a contended lock was acquired")

    async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
        task = asyncio.create_task(contender())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(held) == 1
        assert all(request.method == "POST" for request in requests)
    assert held == set()


async def test_local_waiter_observes_deadline_without_polling(
    lock_server: LockServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, held, requests = lock_server
    monkeypatch.setattr(thread_pr_state, "_LOCK_TIMEOUT_SECONDS", 0.01)
    async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
        with pytest.raises(TimeoutError, match="thread-1"):
            async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
                pytest.fail("a contended lock was acquired")
        assert len(held) == 1
        assert len(requests) == 1
    assert held == set()


async def test_release_failure_is_logged_without_masking_body_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(503, json={"detail": "temporarily unavailable"})
        return httpx.Response(200, json={"thread_id": "lock"})

    async with httpx.AsyncClient(
        base_url="http://langgraph.test", transport=httpx.MockTransport(handle)
    ) as http:
        client = LangGraphClient(http)
        with pytest.raises(ValueError, match="body failed"):
            async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
                raise ValueError("body failed")
        async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
            pass
    assert any(
        record.levelname == "WARNING"
        and getattr(record, "agent_thread_id", None) == "thread-1"
        and record.exc_info is not None
        for record in caplog.records
    )


async def test_body_failure_releases_the_lock(lock_server: LockServer) -> None:
    client, held, _ = lock_server
    with pytest.raises(ValueError, match="body failed"):
        async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
            raise ValueError("body failed")
    assert held == set()
    async with thread_pr_state.agent_thread_pr_state_lock(client, "thread-1"):
        assert len(held) == 1
