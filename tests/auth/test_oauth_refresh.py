import asyncio

from openswe.dashboard import oauth_refresh


async def test_refresh_guard_serializes_a_credential_across_processes(registry_db, monkeypatch):
    order: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first() -> None:
        async with oauth_refresh.refresh_guard("langsmith", "alice"):
            order.append("first in")
            entered.set()
            await release.wait()
            order.append("first out")

    async def second() -> None:
        await entered.wait()
        # Another worker shares only the database lock, never this process's lock.
        monkeypatch.setattr(oauth_refresh, "_local_locks", {})
        async with oauth_refresh.refresh_guard("langsmith", "Alice"):
            order.append("second in")

    tasks = [asyncio.create_task(first()), asyncio.create_task(second())]
    try:
        await entered.wait()
        await asyncio.sleep(0.3)
        assert order == ["first in"]
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
    assert order == ["first in", "first out", "second in"]
