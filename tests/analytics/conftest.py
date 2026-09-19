"""Isolated PostgreSQL fixtures shared across analytics layers."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from agent.database import analytics as database
from agent.database import postgres
from tests.conftest import isolated_schema


async def initialize_database() -> None:
    """What the app lifespan does at startup: migrate, then load the analytics workspace."""
    await postgres.migrate()
    await database.load_workspace()


@pytest.fixture
async def deployment_db(monkeypatch):
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required for PostgreSQL regressions")
    uri = postgres.normalize_uri(uri)
    admin = create_async_engine(uri, isolation_level="AUTOCOMMIT")
    db_name = f"analytics_deployment_test_{uuid4().hex}"
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    test_uri = make_url(uri).set(database=db_name).render_as_string(hide_password=False)
    await postgres.close()
    monkeypatch.setenv("POSTGRES_URI", test_uri)
    try:
        yield
    finally:
        await postgres.close()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{db_name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture
async def analytics_db(monkeypatch):
    uri = os.environ.get("TEST_ANALYTICS_POSTGRES_URI")
    if not uri:
        pytest.skip("TEST_ANALYTICS_POSTGRES_URI is required for PostgreSQL regressions")
    monkeypatch.setenv("ANALYTICS_SUMMARY_VERSION", "1")
    async with isolated_schema(uri, monkeypatch):
        async with postgres.connection() as conn:
            workspace = await conn.scalar(text("SELECT workspace_id FROM deployment_metadata"))
        monkeypatch.setattr(database, "_WORKSPACE_ID", workspace)
        yield workspace, postgres.transaction
