import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.database.store_imports import StoreImport, run_store_import

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_an_import_retires_only_after_a_quiet_day() -> None:
    passes = [StoreImport(moved=3), StoreImport(), StoreImport(), StoreImport(moved=9)]

    async def run() -> StoreImport:
        return passes.pop(0)

    await run_store_import("test", run)
    await run_store_import("test", run)
    async with transaction() as conn:
        await conn.execute(
            text("UPDATE store_import SET last_moved_at = last_moved_at - interval '2 days'")
        )
    await run_store_import("test", run)
    await run_store_import("test", run)

    assert passes == [StoreImport(moved=9)]
