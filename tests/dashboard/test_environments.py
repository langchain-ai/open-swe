from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard import environments as env_store
from agent.dashboard.environments import (
    EnvironmentCreate,
    EnvironmentUpdate,
    environment_prompt,
    environment_snapshot_id,
    slugify,
    snapshot_name_for,
)


def _fake_client() -> tuple[MagicMock, dict[tuple[Any, ...], Any]]:
    store: dict[tuple[Any, ...], Any] = {}
    client = MagicMock()

    async def put_item(ns: list[str], key: str, value: dict[str, Any]) -> None:
        store[(tuple(ns), key)] = value

    async def get_item(ns: list[str], key: str) -> dict[str, Any] | None:
        value = store.get((tuple(ns), key))
        return {"value": value} if value is not None else None

    async def delete_item(ns: list[str], key: str) -> None:
        store.pop((tuple(ns), key), None)

    async def search_items(ns: list[str], limit: int = 100) -> dict[str, Any]:
        items = [
            {"value": value} for (namespace, _key), value in store.items() if namespace == tuple(ns)
        ]
        return {"items": items[:limit]}

    client.store.put_item = AsyncMock(side_effect=put_item)
    client.store.get_item = AsyncMock(side_effect=get_item)
    client.store.delete_item = AsyncMock(side_effect=delete_item)
    client.store.search_items = AsyncMock(side_effect=search_items)
    return client, store


# --- slug + snapshot naming (sync) ---


def test_slugify_normalizes_to_tag_safe_token() -> None:
    assert slugify("  LangSmith Monorepo!  ") == "langsmith-monorepo"


def test_slugify_rejects_names_without_alphanumerics() -> None:
    with pytest.raises(ValueError, match="at least one letter or digit"):
        slugify("---")


def test_snapshot_name_carries_no_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The platform rejects a colon and appends its own `:latest`."""
    monkeypatch.delenv("ENVIRONMENT_SNAPSHOT_PREFIX", raising=False)
    assert snapshot_name_for("monorepo") == "openswe-environment-monorepo"
    assert snapshot_name_for("monorepo", 3) == "openswe-environment-monorepo-3"


def test_snapshot_name_prefix_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT_SNAPSHOT_PREFIX", "acme")
    assert snapshot_name_for("default") == "acme-environment-default"


def test_no_generated_snapshot_name_contains_a_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    """A colon anywhere in the name is rejected by the platform, so never emit one."""
    monkeypatch.setenv("ENVIRONMENT_SNAPSHOT_PREFIX", "acme:v2")
    for attempt in range(1, env_store.CAPTURE_NAME_ATTEMPTS + 1):
        assert ":" not in snapshot_name_for("default", attempt)


def test_create_validates_repo_full_names() -> None:
    create = EnvironmentCreate(
        name="env", repos=["https://github.com/owner/repo.git", "owner/repo"]
    )
    assert create.repos == ["owner/repo"]


def test_snapshot_id_only_resolves_when_ready() -> None:
    assert environment_snapshot_id({"snapshot_status": "capturing", "snapshot_id": "s-1"}) is None
    assert environment_snapshot_id({"snapshot_status": "ready", "snapshot_id": "s-1"}) == "s-1"
    assert environment_snapshot_id(None) is None


def test_environment_prompt_blank_is_none() -> None:
    assert environment_prompt({"prompt": "   "}) is None
    assert environment_prompt({"prompt": " build with make "}) == "build with make"


# --- CRUD (patched store) ---


@pytest.mark.asyncio
async def test_only_the_environment_named_default_is_resolved() -> None:
    client, _ = _fake_client()
    with patch.object(env_store, "get_client", return_value=client):
        await env_store.create_environment(EnvironmentCreate(name="Draft"), "ramon")
        assert await env_store.resolve_default_environment() is None

        await env_store.create_environment(EnvironmentCreate(name="Default"), "ramon")
        resolved = await env_store.resolve_default_environment()

    assert resolved is not None
    assert resolved["slug"] == "default"


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name() -> None:
    client, _ = _fake_client()
    with patch.object(env_store, "get_client", return_value=client):
        await env_store.create_environment(EnvironmentCreate(name="base"), "ramon")
        with pytest.raises(ValueError, match="already exists"):
            await env_store.create_environment(EnvironmentCreate(name="Base"), "ramon")


@pytest.mark.asyncio
async def test_update_writes_only_provided_fields() -> None:
    client, _ = _fake_client()
    with patch.object(env_store, "get_client", return_value=client):
        await env_store.create_environment(
            EnvironmentCreate(name="base", prompt="original", repos=["o/r"]), "ramon"
        )
        updated = await env_store.update_environment("base", EnvironmentUpdate(prompt="replaced"))
        assert updated["prompt"] == "replaced"
        assert updated["repos"] == ["o/r"]


@pytest.mark.asyncio
async def test_update_rejects_a_rename_across_slugs() -> None:
    client, _ = _fake_client()
    with patch.object(env_store, "get_client", return_value=client):
        await env_store.create_environment(EnvironmentCreate(name="draft"), "ramon")
        with pytest.raises(ValueError, match="renaming an environment"):
            await env_store.update_environment("draft", EnvironmentUpdate(name="default"))


@pytest.mark.asyncio
async def test_delete_removes_record_and_snapshot() -> None:
    client, _ = _fake_client()
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "get_client", return_value=client),
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
    ):
        await env_store.create_environment(EnvironmentCreate(name="default"), "ramon")
        await env_store._set_snapshot_state("default", "ready", extra={"snapshot_id": "snap-1"})

        assert await env_store.delete_environment("default") is True
        assert await env_store.resolve_default_environment() is None
        delete_snapshot.assert_awaited_once_with("snap-1")


@pytest.mark.asyncio
async def test_resolve_default_environment_swallows_store_failures() -> None:
    client = MagicMock()
    client.store.get_item = AsyncMock(side_effect=RuntimeError("store down"))
    with patch.object(env_store, "get_client", return_value=client):
        assert await env_store.resolve_default_environment() is None


# --- capture ---


class _FakeSnapshot:
    def __init__(self, snapshot_id: str) -> None:
        self.id = snapshot_id


def _sandbox_client(capture: AsyncMock) -> MagicMock:
    client = MagicMock()
    client.capture_snapshot = capture
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.mark.asyncio
async def test_capture_tags_latest_and_replaces_previous_snapshot() -> None:
    client, _ = _fake_client()
    capture = AsyncMock(return_value=_FakeSnapshot("snap-2"))
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "get_client", return_value=client),
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
        patch(
            "agent.integrations.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await env_store.create_environment(EnvironmentCreate(name="base"), "ramon")
        await env_store._set_snapshot_state("base", "ready", extra={"snapshot_id": "snap-1"})

        record = await env_store.capture_environment_snapshot("base", "sb-123")

    assert capture.await_args is not None
    assert capture.await_args.args == ("sb-123", "openswe-environment-base")
    assert record["snapshot_status"] == "ready"
    assert record["snapshot_id"] == "snap-2"
    assert record["snapshot_name"] == "openswe-environment-base"
    assert record["source_sandbox_id"] == "sb-123"
    delete_snapshot.assert_awaited_once_with("snap-1")


class _NameConflict(Exception):
    """Stands in for the SDK's ResourceAlreadyExistsError (matched by class name)."""


_NameConflict.__name__ = "ResourceAlreadyExistsError"


@pytest.mark.asyncio
async def test_capture_walks_the_name_suffix_past_a_conflict() -> None:
    client, _ = _fake_client()
    capture = AsyncMock(side_effect=[_NameConflict("taken"), _FakeSnapshot("snap-2")])
    with (
        patch.object(env_store, "get_client", return_value=client),
        patch.object(env_store, "_delete_snapshot", AsyncMock()),
        patch(
            "agent.integrations.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await env_store.create_environment(EnvironmentCreate(name="default"), "ramon")
        record = await env_store.capture_environment_snapshot("default", "sb-123")

    assert [call.args[1] for call in capture.await_args_list] == [
        "openswe-environment-default",
        "openswe-environment-default-2",
    ]
    assert record["snapshot_status"] == "ready"
    assert record["snapshot_name"] == "openswe-environment-default-2"


@pytest.mark.asyncio
async def test_failed_recapture_keeps_booting_from_the_previous_snapshot() -> None:
    client, _ = _fake_client()
    capture = AsyncMock(side_effect=RuntimeError("capture exploded"))
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "get_client", return_value=client),
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
        patch(
            "agent.integrations.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await env_store.create_environment(EnvironmentCreate(name="base"), "ramon")
        await env_store._set_snapshot_state("base", "ready", extra={"snapshot_id": "snap-1"})

        with pytest.raises(RuntimeError, match="capture exploded"):
            await env_store.capture_environment_snapshot("base", "sb-123")

        record = await env_store.get_environment("base")

    assert record is not None
    # Still ready, so runs keep booting from snap-1 instead of dropping to the
    # base image; the error rides along in status_message.
    assert record["snapshot_status"] == "ready"
    assert environment_snapshot_id(record) == "snap-1"
    assert record["status_message"] == "capture exploded"
    delete_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_capture_failure_marks_the_environment_failed() -> None:
    client, _ = _fake_client()
    capture = AsyncMock(side_effect=RuntimeError("capture exploded"))
    with (
        patch.object(env_store, "get_client", return_value=client),
        patch(
            "agent.integrations.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await env_store.create_environment(EnvironmentCreate(name="base"), "ramon")

        with pytest.raises(RuntimeError, match="capture exploded"):
            await env_store.capture_environment_snapshot("base", "sb-123")

        record = await env_store.get_environment("base")

    # Nothing to fall back to, so the record says so rather than claiming ready.
    assert record is not None
    assert record["snapshot_status"] == "failed"
    assert environment_snapshot_id(record) is None


@pytest.mark.asyncio
async def test_capture_requires_the_langsmith_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "local")
    client, _ = _fake_client()
    capture = AsyncMock()
    with (
        patch.object(env_store, "get_client", return_value=client),
        patch(
            "agent.integrations.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await env_store.create_environment(EnvironmentCreate(name="base"), "ramon")
        with pytest.raises(RuntimeError, match="SANDBOX_TYPE=langsmith"):
            await env_store.capture_environment_snapshot("base", "sb-123")

    capture.assert_not_awaited()


# --- per-thread selection ---


@pytest.mark.parametrize(
    ("text", "expected_slug", "expected_text"),
    [
        ("env:staging please fix the bug", "staging", "please fix the bug"),
        ("please fix the bug env:staging", "staging", "please fix the bug"),
        ("please fix the bug", None, "please fix the bug"),
        # Not a tag: no word boundary before it.
        ("see env:staging/notes.md", None, "see env:staging/notes.md"),
        ("open env:Staging-Box now", "staging-box", "open now"),
    ],
)
def test_parse_environment_tag(text: str, expected_slug: str | None, expected_text: str) -> None:
    assert env_store.parse_environment_tag(text) == (expected_slug, expected_text)


@pytest.mark.asyncio
async def test_resolve_environment_prefers_the_selection() -> None:
    client, _ = _fake_client()
    with patch.object(env_store, "get_client", return_value=client):
        await env_store.create_environment(EnvironmentCreate(name="default"), "ramon")
        await env_store.create_environment(EnvironmentCreate(name="staging"), "ramon")

        selected = await env_store.resolve_environment("staging")
        assert selected is not None
        assert selected["slug"] == "staging"

        unselected = await env_store.resolve_environment(None)
        assert unselected is not None
        assert unselected["slug"] == "default"

        # A selection that no longer exists falls back rather than failing the run.
        stale = await env_store.resolve_environment("deleted")
        assert stale is not None
        assert stale["slug"] == "default"


@pytest.mark.asyncio
async def test_environment_options_omit_prompts() -> None:
    client, _ = _fake_client()
    with patch.object(env_store, "get_client", return_value=client):
        await env_store.create_environment(
            EnvironmentCreate(name="default", prompt="secret-ish prompt"), "ramon"
        )
        await env_store._set_snapshot_state("default", "ready", extra={"snapshot_id": "snap-1"})
        options = await env_store.list_environment_options()

    assert options == [{"slug": "default", "name": "default", "has_snapshot": True}]
