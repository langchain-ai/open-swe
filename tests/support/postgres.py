"""Per-test PostgreSQL databases cloned from one migrated template per process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from agent.database import postgres

TEST_SCHEMA = "open_swe_test"


class MigratedTemplate:
    """Migrating costs ~0.5s; cloning a migrated template database is a file copy."""

    name: str | None = None
    admin_uri: str | None = None

    @staticmethod
    def admin_engine(uri: str) -> AsyncEngine:
        return create_async_engine(uri, isolation_level="AUTOCOMMIT", poolclass=NullPool)

    @staticmethod
    def database_uri(uri: str, database: str) -> str:
        return make_url(uri).set(database=database).render_as_string(hide_password=False)

    @classmethod
    async def clone(cls, admin: AsyncEngine, uri: str) -> str:
        if cls.name is None:
            name = f"open_swe_test_template_{uuid4().hex}"
            async with admin.connect() as conn:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
            engine = create_async_engine(cls.database_uri(uri, name), poolclass=NullPool)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
                    await conn.run_sync(postgres.upgrade, postgres.load_migrations(), TEST_SCHEMA)
            finally:
                await engine.dispose()
            cls.name, cls.admin_uri = name, uri
        database = f"open_swe_test_{uuid4().hex}"
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{database}" TEMPLATE "{cls.name}"'))
        return database

    @classmethod
    async def drop(cls) -> None:
        if cls.name is None or cls.admin_uri is None:
            return
        admin = cls.admin_engine(cls.admin_uri)
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{cls.name}" WITH (FORCE)'))
        finally:
            await admin.dispose()
        cls.name = None


@asynccontextmanager
async def isolated_database(uri: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Point ``agent.database`` at a fresh, fully migrated database, then drop it.

    The real engine, connection, transaction and session code runs; only the
    engine and the schema name are swapped, so it does not matter which module a
    consumer imported the database API through.
    """
    admin = MigratedTemplate.admin_engine(uri)
    try:
        database = await MigratedTemplate.clone(admin, uri)
        monkeypatch.setenv("POSTGRES_URI", MigratedTemplate.database_uri(uri, database))
        engine = create_async_engine(
            postgres.uri() or uri, connect_args={"server_settings": {"TimeZone": "UTC"}}
        )
        monkeypatch.setattr(postgres, "SCHEMA", TEST_SCHEMA)
        monkeypatch.setattr(postgres, "_ENGINE", engine)
        monkeypatch.setattr(postgres, "_ENGINE_URI", postgres.uri())
        try:
            yield
        finally:
            await postgres.close()
            await engine.dispose()
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
    finally:
        await admin.dispose()
