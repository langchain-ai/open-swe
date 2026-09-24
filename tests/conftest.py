"""Shared pytest fixtures."""

import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent import store as agent_store
from agent.database import postgres
from agent.sandboxes.state import SANDBOX_BACKENDS, SANDBOX_CONNECTIONS
from agent.threads import access, diffs, handlers, listing, proxy, runs, summary
from agent.utils import shared_cache, ttl_cache
from agent.webhooks import common as webhook_common
from agent.workspaces.store import WORKSPACES

_THREAD_MODULES: tuple[ModuleType, ...] = (access, diffs, handlers, listing, proxy, runs, summary)
_MAX_PARAM_ID_CHARS = 40


def pytest_make_parametrize_id(config: pytest.Config, val: object, argname: str) -> str | None:
    """Keep node IDs short; a 100 KB ID line truncates `gh run view --log-failed` output."""
    if isinstance(val, str) and len(val) > _MAX_PARAM_ID_CHARS:
        return f"{argname}-{hashlib.sha256(val.encode()).hexdigest()[:8]}"
    return None


def patch_thread_module(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """Rebind ``name`` in every dashboard thread module that imports it.

    The thread endpoints are split across modules that each hold their own
    binding, so patching one would leave the others pointing at the real thing.
    """
    modules = [module for module in _THREAD_MODULES if hasattr(module, name)]
    if not modules:
        raise AttributeError(f"no dashboard thread module defines {name!r}")
    for module in modules:
        monkeypatch.setattr(module, name, value)


class _FakeStoreNotFoundError(Exception):
    status_code = 404


class FakeStore:
    """In-memory stand-in for the LangGraph Store, in the SDK's item shape.

    Backs the real ``agent.store`` code path, so values round-trip through
    ``model_dump``/``model_validate`` the way they do in production.
    """

    def __init__(self) -> None:
        self.items: dict[tuple[str, ...], dict[str, dict[str, Any]]] = {}
        self.ttls: dict[tuple[tuple[str, ...], str], int | None] = {}

    def seed(self, namespace: Sequence[str], key: str, value: dict[str, Any]) -> None:
        self.items.setdefault(tuple(namespace), {})[key] = dict(value)

    def values(self, namespace: Sequence[str]) -> dict[str, dict[str, Any]]:
        return self.items.get(tuple(namespace), {})

    async def get_item(self, namespace: Sequence[str], key: str) -> dict[str, Any]:
        value = self.values(namespace).get(key)
        if value is None:
            raise _FakeStoreNotFoundError
        return {"value": dict(value)}

    async def put_item(
        self, namespace: Sequence[str], key: str, value: dict[str, Any], ttl: int | None = None
    ) -> None:
        self.seed(namespace, key, value)
        self.ttls[(tuple(namespace), key)] = ttl

    def ttl_minutes(self, namespace: Sequence[str], key: str) -> int | None:
        """How long after its last write the Store deletes the item; ``None`` keeps it forever."""
        return self.ttls.get((tuple(namespace), key))

    async def delete_item(self, namespace: Sequence[str], key: str) -> None:
        self.values(namespace).pop(key, None)

    async def search_items(
        self,
        namespace: Sequence[str],
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        matches = [
            {"value": dict(value)}
            for value in self.values(namespace).values()
            if all(value.get(k) == expected for k, expected in (filter or {}).items())
        ]
        return {"items": matches[offset : offset + limit]}


class FakeStoreClient:
    def __init__(self) -> None:
        self.store = FakeStore()


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """Route every ``agent.store`` access to an in-memory store for this test."""
    client = FakeStoreClient()
    monkeypatch.setattr(agent_store, "store_client", lambda: client)
    return client.store


async def post_signed_github_webhook(
    event_type: str,
    payload: Mapping[str, object],
    *,
    secret: str,
    delivery_id: str | None = None,
) -> httpx.Response:
    """POST a signed GitHub webhook to the real app, on the test's own event loop.

    An in-process ``httpx.AsyncClient`` rather than ``TestClient``: a
    workspace-backed route reads ownership through ``registry_db``'s engine,
    which is bound to the loop the test runs on, while ``TestClient`` drives the
    request from a worker thread with an event loop of its own.
    """
    from agent.api.app import app

    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event_type,
        "X-Hub-Signature-256": f"sha256={signature}",
    }
    if delivery_id is not None:
        headers["X-GitHub-Delivery"] = delivery_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/webhooks/github", content=body, headers=headers)


_TEST_POSTGRES_URI_SETTING = "TEST_ANALYTICS_POSTGRES_URI"


@asynccontextmanager
async def isolated_schema(uri: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Point ``agent.database`` at a fresh, fully migrated schema, then drop it.

    The real engine, connection, transaction and session code runs; only the
    engine and the schema name are swapped, so it does not matter which module a
    consumer imported the database API through.
    """
    monkeypatch.setenv("POSTGRES_URI", uri)
    engine = create_async_engine(
        postgres.uri() or uri, connect_args={"server_settings": {"TimeZone": "UTC"}}
    )
    schema = f"open_swe_test_{uuid4().hex}"
    migrations = postgres.load_migrations()
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
        await conn.run_sync(postgres.upgrade, migrations, schema)
    monkeypatch.setattr(postgres, "SCHEMA", schema)
    monkeypatch.setattr(postgres, "_ENGINE", engine)
    monkeypatch.setattr(postgres, "_ENGINE_URI", postgres.uri())
    try:
        yield
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await engine.dispose()


@pytest.fixture
async def registry_db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """A migrated PostgreSQL schema for pull request and repository rows."""
    uri = os.environ.get(_TEST_POSTGRES_URI_SETTING)
    if not uri:
        pytest.skip(f"{_TEST_POSTGRES_URI_SETTING} is required for PostgreSQL regressions")
    async with isolated_schema(uri, monkeypatch):
        yield


@pytest.fixture
async def registry_db_if_available(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[bool]:
    """``registry_db`` when a database is configured; otherwise the unconfigured path.

    Lets tests whose code under test degrades without PostgreSQL run both ways.
    """
    uri = os.environ.get(_TEST_POSTGRES_URI_SETTING)
    if not uri:
        monkeypatch.delenv("POSTGRES_URI", raising=False)
        yield False
        return
    async with isolated_schema(uri, monkeypatch):
        yield True


@pytest.fixture
def findings_from_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve dashboard findings from thread metadata, as for threads not yet in PostgreSQL."""
    from agent.review import reviews
    from agent.review.findings import Finding, coerce_findings

    async def read(metadata_by_thread: Mapping[str, dict[str, Any]]) -> dict[str, list[Finding]]:
        return {
            thread_id: coerce_findings(metadata.get("findings"))
            for thread_id, metadata in metadata_by_thread.items()
        }

    monkeypatch.setattr(reviews, "findings_by_thread", read)


@pytest.fixture
def allowed_bot(fake_store: FakeStore) -> dict[str, Any]:
    bot = {
        "team_id": "T123",
        "bot_id": "B123",
        "user_id": "U123",
        "app_id": "A123",
        "name": "Release bot",
        "created_by": "alice",
        "created_at": "2026-09-09",
    }
    fake_store.seed(["allowed_slack_bots"], "T123:B123", bot)
    return bot


@pytest.fixture(autouse=True)
def _default_github_login_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "test-user,trusted-user,reviewer")


@pytest.fixture(autouse=True)
def _no_bundled_dashboard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Serve no dashboard build by default, whatever ``ui/.output`` holds locally."""
    monkeypatch.setenv("DASHBOARD_STATIC_DIR", str(tmp_path / "no-dashboard-build"))


@pytest.fixture(autouse=True)
def _reset_ttl_cache() -> Iterator[None]:
    """Keep the process-global TTL cache from leaking workspace settings between tests,
    and one test's pending shared-cache refresh from writing into the next."""
    ttl_cache.clear()
    shared_cache.clear()
    yield
    ttl_cache.clear()
    shared_cache.clear()


@pytest.fixture(autouse=True)
def _reset_sandbox_registries() -> Iterator[None]:
    """Both sandbox registries are process globals; a leaked handle would let one
    test's sandbox answer for the next test's thread."""
    SANDBOX_BACKENDS.clear()
    SANDBOX_CONNECTIONS.clear()
    yield
    SANDBOX_BACKENDS.clear()
    SANDBOX_CONNECTIONS.clear()


@pytest.fixture(autouse=True)
def _workspace_store_import_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat the startup import of LangGraph Store workspaces as done.

    Tests do not run the application lifespan, and until that import succeeds
    :func:`agent.workspaces.routing.repo_is_routable` fails closed rather than
    reading an empty table as "nobody owns this repository". A test about that
    path sets the flag back to ``False`` itself.
    """
    monkeypatch.setattr(WORKSPACES, "import_completed", True)
    monkeypatch.setattr(WORKSPACES, "unimported_repos", frozenset())


@pytest.fixture(autouse=True)
def _default_enable_auto_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat automatic reviews as enabled for every repo by default.

    The dashboard's opt-in list (loaded by :func:`agent.review.enabled_repos.is_review_repo_enabled`)
    is empty in the test environment because there is no live LangGraph Store.

    Tests targeting the automatic-review gate should override this fixture or set
    ``monkeypatch.setattr(webhook_common, "is_review_repo_enabled", ...)`` to a stricter stub.
    """

    async def _enabled(_owner: str, _name: str) -> bool:
        return True

    monkeypatch.setattr(webhook_common, "is_review_repo_enabled", _enabled)


@pytest.fixture
def slack_api(monkeypatch: pytest.MonkeyPatch):
    from agent.slack import channels, client, code_channels, http
    from tests.support.slack_api import slack_api_server

    monkeypatch.setenv("SLACK_BOT_TOKEN", "test-slack-token")
    with slack_api_server() as api:
        monkeypatch.setattr(http, "SLACK_API_BASE_URL", api.base_url)
        monkeypatch.setattr(client, "SLACK_BOT_TOKEN", "test-slack-token")
        monkeypatch.setattr(code_channels, "SLACK_BOT_TOKEN", "test-slack-token")
        monkeypatch.setattr(channels, "SLACK_BOT_TOKEN", "test-slack-token")
        yield api
