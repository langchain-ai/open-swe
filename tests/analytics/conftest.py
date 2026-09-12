"""Isolated PostgreSQL fixtures shared across analytics layers."""

import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from agent.analytics import database


@pytest.fixture
async def deployment_db(monkeypatch):
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required for PostgreSQL regressions")
    admin = create_async_engine(uri, isolation_level="AUTOCOMMIT")
    db_name = f"analytics_deployment_test_{uuid4().hex}"
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    test_uri = make_url(uri).set(database=db_name).render_as_string(hide_password=False)
    await database.close()
    monkeypatch.setenv("POSTGRES_URI", test_uri)
    try:
        yield
    finally:
        await database.close()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture
async def analytics_db(monkeypatch):
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required for PostgreSQL regressions")
    engine = create_async_engine(uri)
    schema = f"analytics_test_{uuid4().hex}"
    monkeypatch.setenv("ANALYTICS_SUMMARY_VERSION", "1")
    migrations = database._load_migrations()
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
        await conn.run_sync(database._upgrade, migrations, schema)
        workspace = await conn.scalar(
            text(f"SELECT workspace_id FROM {schema}.deployment_metadata")
        )
    monkeypatch.setattr(database, "_WORKSPACE_ID", workspace)

    @asynccontextmanager
    async def transaction():
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
            await conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            yield conn

    monkeypatch.setattr(database, "transaction", transaction)
    monkeypatch.setattr(database, "connection", transaction)
    try:
        yield workspace, transaction
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await engine.dispose()
