from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from agent import store as agent_store
from agent.workspaces import store as env_store
from agent.workspaces.store import (
    WORKSPACES,
    Workspace,
    WorkspaceCreate,
    WorkspaceUpdate,
    default_snapshot_name_for,
    log_excerpt,
    slugify,
)
from tests.conftest import FakeStore


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

    async def search_items(
        ns: list[str],
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        items = [
            {"value": value} for (namespace, _key), value in store.items() if namespace == tuple(ns)
        ]
        return {"items": items[offset : offset + limit]}

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


def test_snapshot_name_prefix_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKSPACE_SNAPSHOT_PREFIX", "acme")
    assert default_snapshot_name_for("default") == "acme-environment-default"


def test_no_generated_snapshot_name_contains_a_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    """A colon separates name from tag, so a name carrying one is unaddressable."""
    # The pre-rename variable name still works as an alias.
    monkeypatch.setenv("ENVIRONMENT_SNAPSHOT_PREFIX", "acme:v2")
    assert ":" not in default_snapshot_name_for("default")


def test_a_stored_snapshot_name_wins_over_the_derived_one() -> None:
    assert (
        Workspace(slug="base", snapshot_name="acme-monorepo").published_snapshot_name
        == "acme-monorepo"
    )
    assert Workspace(slug="base").published_snapshot_name == "openswe-environment-base"


def test_snapshot_name_rejects_a_tag_separator() -> None:
    with pytest.raises(ValidationError, match="must not contain a colon"):
        WorkspaceCreate(name="env", snapshot_name="acme-monorepo:v2")


def test_scripts_are_stripped_on_the_way_in() -> None:
    record = Workspace(slug="base", setup_script="  make setup  ", update_script="   ")
    assert record.setup_script == "make setup"
    assert not record.update_script


def test_log_excerpt_keeps_the_head_and_the_tail() -> None:
    excerpt = log_excerpt("\n".join(str(n) for n in range(50)), lines=2)
    assert excerpt == "0\n1\n… 46 lines omitted …\n48\n49"
    assert log_excerpt("one\ntwo", lines=2) == "one\ntwo"
    assert log_excerpt("   ") is None


def test_create_validates_repo_full_names() -> None:
    create = WorkspaceCreate(name="env", repos=["https://github.com/owner/repo.git", "owner/repo"])
    assert create.repos == ["owner/repo"]


def test_sandbox_resources_require_positive_integers() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        WorkspaceCreate(name="env", mem_bytes=0)
    with pytest.raises(ValueError, match="greater than 0"):
        WorkspaceUpdate(vcpus=-1)


def test_workspace_sandbox_resources_omits_invalid_stored_values() -> None:
    ready = Workspace(
        slug="env",
        mem_bytes=16 * 1024**3,
        vcpus=8,
        fs_capacity_bytes=256 * 1024**3,
    )
    assert ready.sandbox_resources() == {
        "mem_bytes": 16 * 1024**3,
        "vcpus": 8,
        "fs_capacity_bytes": 256 * 1024**3,
    }
    assert Workspace(slug="env").sandbox_resources() == {}
    assert Workspace(slug="env", mem_bytes=-1).sandbox_resources() == {}


def test_create_params_accept_non_sensitive_runtime_and_proxy_settings() -> None:
    params = {
        "_internal_runtime": "v2",
        "proxy_config": {
            "rules": [{"name": "public-api", "match_hosts": ["example.com"]}],
        },
    }
    create = WorkspaceCreate(name="env", create_params=params)

    assert create.create_params == params
    assert Workspace(slug="env", create_params=params).sandbox_create_params() == params


@pytest.mark.parametrize(
    "create_params",
    [
        {"env_vars": {"API_TOKEN": "sensitive"}},
        {"env_vars": {"OPENAI_API_KEY": "sensitive"}},
        {"clientSecret": "sensitive"},
        {
            "proxy_config": {
                "rules": [
                    {"headers": [{"name": "Authorization", "type": "opaque", "value": "sensitive"}]}
                ]
            }
        },
        {
            "proxy_config": {
                "rules": [{"headers": [{"name": "X-OpenAI-Api-Key", "value": "sensitive"}]}]
            }
        },
    ],
)
def test_create_params_reject_persisted_secrets(create_params: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must not contain secrets"):
        WorkspaceCreate(name="env", create_params=create_params)


@pytest.mark.parametrize(
    "create_params",
    [
        {"proxy_config": "enabled"},
        {"proxy_config": {"rules": {"name": "invalid"}}},
    ],
)
def test_create_params_validate_proxy_config_shape(create_params: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="proxy_config"):
        WorkspaceCreate(name="env", create_params=create_params)


def test_create_params_enforce_serialized_size_limit() -> None:
    with pytest.raises(ValueError, match="at most"):
        WorkspaceCreate(
            name="env",
            create_params={"metadata": "x" * env_store.CREATE_PARAMS_MAX_CHARS},
        )


def test_a_capture_in_flight_keeps_serving_the_previous_snapshot() -> None:
    """The new id lands only on success, so the old one is still what runs want."""
    capturing = Workspace(slug="e", snapshot_status="capturing", snapshot_id="s-1")
    assert capturing.ready_snapshot_id == "s-1"
    # Nothing captured yet: a first capture in flight has nothing to fall back to.
    assert Workspace(slug="e", snapshot_status="capturing").ready_snapshot_id is None


def test_workspace_prompt_blank_is_none() -> None:
    assert Workspace(slug="e", prompt="   ").instructions is None
    assert Workspace(slug="e", prompt=" build with make ").instructions == "build with make"


# --- CRUD (patched store) ---


@pytest.mark.asyncio
async def test_only_the_workspace_named_default_is_resolved(fake_store: FakeStore) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Draft"), "ramon")
    assert await env_store.load_default_workspace() is None

    await WORKSPACES.create(WorkspaceCreate(name="Default"), "ramon")
    resolved = await env_store.load_default_workspace()

    assert resolved is not None
    assert resolved.slug == "default"


@pytest.mark.asyncio
async def test_create_rejects_duplicate_name(fake_store: FakeStore) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="base"), "ramon")
    with pytest.raises(ValueError, match="already exists"):
        await WORKSPACES.create(WorkspaceCreate(name="Base"), "ramon")


@pytest.mark.asyncio
async def test_update_writes_only_provided_fields(fake_store: FakeStore) -> None:
    await WORKSPACES.create(
        WorkspaceCreate(
            name="base",
            prompt="original",
            repos=["o/r"],
            mem_bytes=8 * 1024**3,
            vcpus=4,
            fs_capacity_bytes=128 * 1024**3,
            create_params={"_internal_runtime": "v2"},
        ),
        "ramon",
    )
    updated = await WORKSPACES.apply_update("base", WorkspaceUpdate(prompt="replaced", vcpus=8))
    assert updated.prompt == "replaced"
    assert updated.repos == ["o/r"]
    assert updated.mem_bytes == 8 * 1024**3
    assert updated.vcpus == 8
    assert updated.fs_capacity_bytes == 128 * 1024**3
    assert updated.create_params == {"_internal_runtime": "v2"}

    cleared = await WORKSPACES.apply_update(
        "base",
        WorkspaceUpdate(mem_bytes=None, create_params={}),
    )
    assert cleared.mem_bytes is None
    assert cleared.vcpus == 8
    assert cleared.fs_capacity_bytes == 128 * 1024**3
    assert cleared.create_params == {}


@pytest.mark.asyncio
async def test_update_rejects_a_rename_across_slugs(fake_store: FakeStore) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="draft"), "ramon")
    with pytest.raises(ValueError, match="renaming a workspace"):
        await WORKSPACES.apply_update("draft", WorkspaceUpdate(name="default"))


@pytest.mark.asyncio
async def test_delete_removes_record_and_snapshot(fake_store: FakeStore) -> None:
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="default"), "ramon")
        await WORKSPACES.mark_captured(
            "default",
            snapshot_id="snap-1",
            snapshot_name="prior",
            source_sandbox_id="sb-prior",
        )

        assert await WORKSPACES.remove("default") is True
        assert await env_store.load_default_workspace() is None
        delete_snapshot.assert_awaited_once_with("snap-1")


@pytest.mark.asyncio
async def test_load_default_workspace_swallows_store_failures(fake_store: FakeStore) -> None:
    client = MagicMock()
    client.store.get_item = AsyncMock(side_effect=RuntimeError("store down"))
    with patch.object(agent_store, "store_client", return_value=client):
        assert await env_store.load_default_workspace() is None


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
async def test_capture_tags_latest_and_replaces_previous_snapshot(fake_store: FakeStore) -> None:
    capture = AsyncMock(return_value=_FakeSnapshot("snap-2"))
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base"), "ramon")
        # A prior capture published under the workspace's own name, as any real
        # one would: the name is the address, and only the tag moves.
        await WORKSPACES.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-prior",
        )

        record = await env_store.capture_workspace_snapshot("base", "sb-123")

    assert capture.await_args is not None
    assert capture.await_args.args == ("sb-123", "openswe-environment-base")
    assert record.snapshot_tag == "latest"
    assert record.snapshot_status == "ready"
    assert record.snapshot_id == "snap-2"
    assert record.snapshot_name == "openswe-environment-base"
    assert record.source_sandbox_id == "sb-123"
    delete_snapshot.assert_awaited_once_with("snap-1")


@pytest.mark.asyncio
async def test_capture_publishes_under_the_workspaces_own_name(fake_store: FakeStore) -> None:
    """A stored name is the address; the tag is what each refresh moves."""
    capture = AsyncMock(return_value=_FakeSnapshot("snap-2"))
    with (
        patch.object(env_store, "_delete_snapshot", AsyncMock()),
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(name="base", snapshot_name="acme-monorepo"), "ramon"
        )
        record = await env_store.capture_workspace_snapshot("base", "sb-123")

    assert capture.await_args is not None
    assert capture.await_args.args == ("sb-123", "acme-monorepo")
    assert record.snapshot_name == "acme-monorepo"
    assert record.snapshot_tag == "latest"


@pytest.mark.asyncio
async def test_failed_recapture_keeps_booting_from_the_previous_snapshot(
    fake_store: FakeStore,
) -> None:
    capture = AsyncMock(side_effect=RuntimeError("capture exploded"))
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base"), "ramon")
        await WORKSPACES.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="prior",
            source_sandbox_id="sb-prior",
        )

        with pytest.raises(RuntimeError, match="capture exploded"):
            await env_store.capture_workspace_snapshot("base", "sb-123")

        record = await WORKSPACES.get("base")

    assert record is not None
    # Still ready, so runs keep booting from snap-1 instead of dropping to the
    # base image; the error rides along in status_message.
    assert record.snapshot_status == "ready"
    assert record.ready_snapshot_id == "snap-1"
    assert record.status_message == "capture exploded"
    delete_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_capture_failure_marks_the_workspace_failed(fake_store: FakeStore) -> None:
    capture = AsyncMock(side_effect=RuntimeError("capture exploded"))
    with (
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base"), "ramon")

        with pytest.raises(RuntimeError, match="capture exploded"):
            await env_store.capture_workspace_snapshot("base", "sb-123")

        record = await WORKSPACES.get("base")

    # Nothing to fall back to, so the record says so rather than claiming ready.
    assert record is not None
    assert record.snapshot_status == "failed"
    assert record.ready_snapshot_id is None


@pytest.mark.asyncio
async def test_capture_requires_the_langsmith_provider(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "local")
    capture = AsyncMock()
    with (
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base"), "ramon")
        with pytest.raises(RuntimeError, match="SANDBOX_TYPE=langsmith"):
            await env_store.capture_workspace_snapshot("base", "sb-123")

    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_clearing_create_params_with_null_stays_readable(
    fake_store: FakeStore,
) -> None:
    """An explicit ``create_params: null`` must not poison the record.

    The store mutates records in place, so an unvalidated null would only
    surface on the next read — as a ValidationError that makes the workspace
    unresolvable, unupdatable, and invisible to listings.
    """
    await WORKSPACES.create(
        WorkspaceCreate(name="base", create_params={"_internal_runtime": "v2"}), "ramon"
    )

    updated = await WORKSPACES.apply_update("base", WorkspaceUpdate(create_params=None))

    assert updated.create_params == {}
    reread = await WORKSPACES.get("base")
    assert reread is not None
    assert reread.create_params == {}
    assert [record.slug for record in await WORKSPACES.list_all()] == ["base"]


@pytest.mark.asyncio
async def test_a_record_stored_with_null_create_params_is_still_readable(
    fake_store: FakeStore,
) -> None:
    """Records written before create_params was modelled can hold a null."""
    fake_store.seed(
        env_store.WORKSPACES_NAMESPACE,
        "legacy",
        {"slug": "legacy", "name": "legacy", "create_params": None},
    )

    record = await WORKSPACES.get("legacy")

    assert record is not None
    assert record.create_params == {}
    assert record.sandbox_create_params() == {}


def test_assignment_is_validated() -> None:
    record = Workspace(slug="base")
    with pytest.raises(ValidationError):
        record.vcpus = "not-an-int"  # type: ignore[assignment]


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
def test_parse_workspace_tag(text: str, expected_slug: str | None, expected_text: str) -> None:
    assert env_store.parse_workspace_tag(text) == (expected_slug, expected_text)


@pytest.mark.asyncio
async def test_load_workspace_prefers_the_selection(fake_store: FakeStore) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="default"), "ramon")
    await WORKSPACES.create(WorkspaceCreate(name="staging"), "ramon")

    selected = await env_store.load_workspace("staging")
    assert selected is not None
    assert selected.slug == "staging"

    unselected = await env_store.load_workspace(None)
    assert unselected is not None
    assert unselected.slug == "default"

    # A selection that no longer exists falls back rather than failing the run.
    stale = await env_store.load_workspace("deleted")
    assert stale is not None
    assert stale.slug == "default"


@pytest.mark.asyncio
async def test_workspace_options_omit_admin_only_settings(fake_store: FakeStore) -> None:
    await WORKSPACES.create(
        WorkspaceCreate(
            name="default",
            prompt="secret-ish prompt",
            create_params={"_internal_runtime": "v2"},
        ),
        "ramon",
    )
    await WORKSPACES.mark_captured(
        "default",
        snapshot_id="snap-1",
        snapshot_name="prior",
        source_sandbox_id="sb-prior",
    )
    await WORKSPACES.mark_refresh_settled("default", "success", log="+ TOKEN=hunter2\ndone")
    options = await env_store.list_workspace_options()

    # No prompt, no snapshot id — and no log: `bash -x` expands arguments, so a
    # script that put a credential on a command line has written it there.
    assert options == [
        {
            "slug": "default",
            "name": "default",
            "has_snapshot": True,
            "refresh_status": "success",
            "refresh_kind": None,
            "refresh_finished_at": options[0]["refresh_finished_at"],
            "refresh_error": None,
            "refresh_steps": [],
        }
    ]
    assert options[0]["refresh_finished_at"]

    admin_view = await env_store.list_workspace_options(include_logs=True)
    assert admin_view[0]["refresh_log_excerpt"] == "+ TOKEN=hunter2\ndone"


@pytest.mark.asyncio
async def test_publish_writes_definition_and_image_together(fake_store: FakeStore) -> None:
    """One put carries both, so a record can never show a new definition on an old image."""
    record = await WORKSPACES.publish(
        "base",
        WorkspaceCreate(name="base", prompt="p1", setup_script="make setup"),
        snapshot_id="snap-1",
        snapshot_name="openswe-environment-base",
        source_sandbox_id="sb-1",
        created_by="ramon",
    )
    assert (record.prompt, record.snapshot_id, record.snapshot_status) == ("p1", "snap-1", "ready")
    assert record.created_by == "ramon"

    updated = await WORKSPACES.publish(
        "base",
        WorkspaceUpdate(prompt="p2"),
        snapshot_id="snap-2",
        snapshot_name="openswe-environment-base",
        source_sandbox_id="sb-2",
        created_by="ramon",
    )
    stored = await WORKSPACES.get("base")
    assert stored is not None
    assert (stored.prompt, stored.snapshot_id) == ("p2", "snap-2")
    assert stored.setup_script == "make setup"
    assert updated.source_sandbox_id == "sb-2"


@pytest.mark.asyncio
async def test_publish_refuses_a_create_over_an_existing_workspace(
    fake_store: FakeStore,
) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="base"), "ramon")
    with pytest.raises(ValueError, match="already exists"):
        await WORKSPACES.publish(
            "base",
            WorkspaceCreate(name="base"),
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-1",
            created_by="ramon",
        )
