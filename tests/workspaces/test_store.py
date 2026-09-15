from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from agent.database import postgres
from agent.github.repositories import Repository
from agent.store import now_iso
from agent.workspaces import store as env_store
from agent.workspaces.rows import WorkspaceRepositoryRow, WorkspaceRow
from agent.workspaces.store import (
    LEGACY_ENVIRONMENTS_NAMESPACE,
    WORKSPACES,
    WORKSPACES_NAMESPACE,
    RefreshStep,
    Workspace,
    WorkspaceCreate,
    WorkspaceUpdate,
    default_snapshot_name_for,
    import_store_records,
    log_excerpt,
    slugify,
)
from tests.conftest import FakeStore

# --- slug + snapshot naming (sync) ---


def test_slugify_normalizes_to_tag_safe_token() -> None:
    assert slugify("  LangSmith Monorepo!  ") == "langsmith-monorepo"


def test_slugify_rejects_names_without_alphanumerics() -> None:
    with pytest.raises(ValueError, match="at least one letter or digit"):
        slugify("---")


def test_snapshot_name_prefix_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT_SNAPSHOT_PREFIX", "acme")
    assert default_snapshot_name_for("default") == "acme-environment-default"


def test_no_generated_snapshot_name_contains_a_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    """A colon separates name from tag, so a name carrying one is unaddressable."""
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


def test_repos_are_deduped_however_they_are_capitalized() -> None:
    """One ``repository`` row per name, so two spellings cannot both be bound."""
    create = WorkspaceCreate(name="env", repos=["Acme/API", "acme/api"])
    assert create.repos == ["Acme/API"]


def test_sandbox_resources_require_positive_integers() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        WorkspaceCreate(name="env", mem_bytes=0)
    with pytest.raises(ValueError, match="greater than 0"):
        WorkspaceUpdate(vcpus=-1)


def test_environment_sandbox_resources_omits_invalid_stored_values() -> None:
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


def test_environment_prompt_blank_is_none() -> None:
    assert Workspace(slug="e", prompt="   ").instructions is None
    assert Workspace(slug="e", prompt=" build with make ").instructions == "build with make"


# --- CRUD (PostgreSQL) ---


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_only_the_environment_named_default_is_resolved() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Draft", repos=["acme/draft"]), "ramon")
    assert await env_store.load_default_workspace() is None

    await WORKSPACES.create(WorkspaceCreate(name="Default"), "ramon")
    resolved = await env_store.load_default_workspace()

    assert resolved is not None
    assert resolved.slug == "default"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_create_rejects_duplicate_name() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="base", repos=["acme/base"]), "ramon")
    with pytest.raises(ValueError, match="already exists"):
        await WORKSPACES.create(WorkspaceCreate(name="Base", repos=["acme/base"]), "ramon")


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_update_writes_only_provided_fields() -> None:
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
@pytest.mark.usefixtures("registry_db")
async def test_update_rejects_a_rename_across_slugs() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="draft", repos=["acme/draft"]), "ramon")
    with pytest.raises(ValueError, match="renaming a workspace"):
        await WORKSPACES.apply_update("draft", WorkspaceUpdate(name="default"))


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_delete_removes_record_and_snapshot() -> None:
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
async def test_load_default_workspace_swallows_database_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sandbox is being created; no workspace beats failing the run."""
    monkeypatch.delenv("POSTGRES_URI", raising=False)
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
@pytest.mark.usefixtures("registry_db")
async def test_capture_tags_latest_and_replaces_previous_snapshot() -> None:
    capture = AsyncMock(return_value=_FakeSnapshot("snap-2"))
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base", repos=["acme/base"]), "ramon")
        # A prior capture published under the environment's own name, as any real
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
@pytest.mark.usefixtures("registry_db")
async def test_capture_publishes_under_the_environments_own_name() -> None:
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
            WorkspaceCreate(name="base", repos=["acme/base"], snapshot_name="acme-monorepo"),
            "ramon",
        )
        record = await env_store.capture_workspace_snapshot("base", "sb-123")

    assert capture.await_args is not None
    assert capture.await_args.args == ("sb-123", "acme-monorepo")
    assert record.snapshot_name == "acme-monorepo"
    assert record.snapshot_tag == "latest"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_failed_recapture_keeps_booting_from_the_previous_snapshot() -> None:
    capture = AsyncMock(side_effect=RuntimeError("capture exploded"))
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base", repos=["acme/base"]), "ramon")
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
@pytest.mark.usefixtures("registry_db")
async def test_first_capture_failure_marks_the_environment_failed() -> None:
    capture = AsyncMock(side_effect=RuntimeError("capture exploded"))
    with (
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base", repos=["acme/base"]), "ramon")

        with pytest.raises(RuntimeError, match="capture exploded"):
            await env_store.capture_workspace_snapshot("base", "sb-123")

        record = await WORKSPACES.get("base")

    # Nothing to fall back to, so the record says so rather than claiming ready.
    assert record is not None
    assert record.snapshot_status == "failed"
    assert record.ready_snapshot_id is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_capture_requires_the_langsmith_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "local")
    capture = AsyncMock()
    with (
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base", repos=["acme/base"]), "ramon")
        with pytest.raises(RuntimeError, match="SANDBOX_TYPE=langsmith"):
            await env_store.capture_workspace_snapshot("base", "sb-123")

    capture.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_update_clearing_create_params_with_null_stays_readable() -> None:
    """An explicit ``create_params: null`` must not poison the record.

    The store mutates records in place, so an unvalidated null would only
    surface on the next read — as a ValidationError that makes the environment
    unresolvable, unupdatable, and invisible to listings.
    """
    await WORKSPACES.create(
        WorkspaceCreate(
            name="base", repos=["acme/base"], create_params={"_internal_runtime": "v2"}
        ),
        "ramon",
    )

    updated = await WORKSPACES.apply_update("base", WorkspaceUpdate(create_params=None))

    assert updated.create_params == {}
    reread = await WORKSPACES.get("base")
    assert reread is not None
    assert reread.create_params == {}
    assert [record.slug for record in await WORKSPACES.list_all()] == ["base"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_an_imported_record_with_null_create_params_is_still_readable(
    fake_store: FakeStore,
) -> None:
    """Records written before create_params was modelled can hold a null."""
    fake_store.seed(
        WORKSPACES_NAMESPACE,
        "legacy",
        {"slug": "legacy", "name": "legacy", "create_params": None},
    )
    assert await import_store_records() == 1

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
@pytest.mark.usefixtures("registry_db")
async def test_load_workspace_prefers_the_selection() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="default"), "ramon")
    await WORKSPACES.create(WorkspaceCreate(name="staging", repos=["acme/staging"]), "ramon")

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
@pytest.mark.usefixtures("registry_db")
async def test_environment_options_omit_admin_only_settings() -> None:
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
            "repos": [],
            "slack_channel_ids": [],
            "is_default": True,
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
@pytest.mark.usefixtures("registry_db")
async def test_publish_writes_definition_and_image_together() -> None:
    """One put carries both, so a record can never show a new definition on an old image."""
    record = await WORKSPACES.publish(
        "base",
        WorkspaceCreate(name="base", repos=["acme/base"], prompt="p1", setup_script="make setup"),
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
@pytest.mark.usefixtures("registry_db")
async def test_publish_refuses_a_create_over_an_existing_environment() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="base", repos=["acme/base"]), "ramon")
    with pytest.raises(ValueError, match="already exists"):
        await WORKSPACES.publish(
            "base",
            WorkspaceCreate(name="base"),
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-1",
            created_by="ramon",
        )


# --- Slack channels, uniqueness, and the one-off Store import ---


@pytest.mark.usefixtures("registry_db")
async def test_create_rejects_repo_owned_by_another_workspace() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["acme/api"]), "alice")
    with pytest.raises(ValueError, match="acme/api already belongs to workspace core"):
        await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["ACME/API"]), "alice")


@pytest.mark.usefixtures("registry_db")
async def test_update_rejects_slack_channel_owned_by_another_workspace() -> None:
    await WORKSPACES.create(
        WorkspaceCreate(name="Core", repos=["acme/api"], slack_channel_ids=["C123"]), "alice"
    )
    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "alice")
    with pytest.raises(ValueError, match="C123 already belongs to workspace core"):
        await WORKSPACES.apply_update("oss", WorkspaceUpdate(slack_channel_ids=["c123"]))


@pytest.mark.usefixtures("registry_db")
async def test_non_default_workspace_requires_a_repo() -> None:
    with pytest.raises(ValueError, match="at least one repository"):
        await WORKSPACES.create(WorkspaceCreate(name="Empty"), "alice")
    record = await WORKSPACES.create(WorkspaceCreate(name="Default"), "alice")
    assert record.slug == "default" and record.repos == []


@pytest.mark.usefixtures("registry_db")
async def test_slack_channel_ids_are_normalized() -> None:
    record = await WORKSPACES.create(
        WorkspaceCreate(name="Core", repos=["acme/api"], slack_channel_ids=[" c123 ", "C123"]),
        "alice",
    )
    assert record.slack_channel_ids == ["C123"]


@pytest.mark.usefixtures("registry_db")
async def test_stored_records_are_imported_from_both_namespaces(fake_store: FakeStore) -> None:
    fake_store.seed(
        LEGACY_ENVIRONMENTS_NAMESPACE,
        "default",
        {"slug": "default", "name": "Default", "prompt": "hi", "repos": []},
    )
    fake_store.seed(
        WORKSPACES_NAMESPACE,
        "oss",
        {"slug": "oss", "name": "OSS", "repos": ["acme/oss"], "slack_channel_ids": ["C0SS"]},
    )

    assert await import_store_records() == 2

    stored = {record.slug: record for record in await WORKSPACES.list_all()}
    assert sorted(stored) == ["default", "oss"]
    assert stored["default"].prompt == "hi"
    assert stored["oss"].repos == ["acme/oss"]
    assert stored["oss"].slack_channel_ids == ["C0SS"]
    # Consumed, not merely copied, so neither namespace can write them back.
    assert fake_store.values(WORKSPACES_NAMESPACE) == {}
    assert fake_store.values(LEGACY_ENVIRONMENTS_NAMESPACE) == {}


@pytest.mark.usefixtures("registry_db")
async def test_one_unimportable_record_does_not_stop_the_others(fake_store: FakeStore) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["acme/api"]), "alice")
    fake_store.seed(
        WORKSPACES_NAMESPACE, "taken", {"slug": "taken", "name": "Taken", "repos": ["acme/api"]}
    )
    fake_store.seed(
        WORKSPACES_NAMESPACE, "oss", {"slug": "oss", "name": "OSS", "repos": ["acme/oss"]}
    )

    assert await import_store_records() == 1

    assert sorted(record.slug for record in await WORKSPACES.list_all()) == ["core", "oss"]
    # The one whose repository another workspace owns stays where it is.
    assert sorted(fake_store.values(WORKSPACES_NAMESPACE)) == ["taken"]


@pytest.mark.usefixtures("registry_db")
async def test_importing_twice_imports_nothing_the_second_time(fake_store: FakeStore) -> None:
    fake_store.seed(
        WORKSPACES_NAMESPACE, "oss", {"slug": "oss", "name": "OSS", "repos": ["acme/oss"]}
    )

    assert await import_store_records() == 1
    assert await import_store_records() == 0
    assert [record.slug for record in await WORKSPACES.list_all()] == ["oss"]


@pytest.mark.usefixtures("registry_db")
async def test_deleting_an_imported_workspace_does_not_resurrect_it(
    fake_store: FakeStore,
) -> None:
    fake_store.seed(
        LEGACY_ENVIRONMENTS_NAMESPACE,
        "oss",
        {"slug": "oss", "name": "OSS", "prompt": "hi", "repos": ["acme/oss"]},
    )
    assert await import_store_records() == 1

    assert await WORKSPACES.remove("oss") is True

    assert await import_store_records() == 0
    assert await WORKSPACES.list_all() == []
    assert await WORKSPACES.get("oss") is None


@pytest.mark.usefixtures("registry_db")
async def test_list_all_skips_a_row_that_fails_to_validate() -> None:
    """A hand-edited or pre-model row must not take the whole listing down.

    ``refresh_steps`` is ``jsonb`` with no schema of its own; a step missing
    the required ``label`` field fails ``RefreshStep`` validation the same way
    an older release's stray write would.
    """
    await WORKSPACES.create(WorkspaceCreate(name="Healthy", repos=["acme/healthy"]), "ramon")
    await WORKSPACES.create(WorkspaceCreate(name="Corrupt", repos=["acme/corrupt"]), "ramon")
    async with postgres.session() as session:
        await session.execute(
            text(
                "UPDATE workspace SET refresh_steps = '[{\"bogus\": 1}]'::jsonb WHERE slug = :slug"
            ),
            {"slug": "corrupt"},
        )

    assert [record.slug for record in await WORKSPACES.list_all()] == ["healthy"]


@pytest.mark.usefixtures("registry_db")
async def test_get_reads_an_unreadable_row_as_a_missing_workspace() -> None:
    """A corrupt row must not raise at every caller that resolves a workspace."""
    await WORKSPACES.create(WorkspaceCreate(name="Corrupt", repos=["acme/corrupt"]), "ramon")
    async with postgres.session() as session:
        await session.execute(
            text(
                "UPDATE workspace SET refresh_steps = '[{\"bogus\": 1}]'::jsonb WHERE slug = :slug"
            ),
            {"slug": "corrupt"},
        )

    assert await WORKSPACES.get("corrupt") is None


# --- rows ---


def _fully_populated(now: str) -> Workspace:
    """A record with nothing left at its default, so a dropped field shows up."""
    return Workspace(
        slug="base",
        name="Base",
        prompt="build with make",
        setup_script="make setup",
        update_script="git pull",
        base_snapshot_id="snap-base",
        repos=["acme/api"],
        slack_channel_ids=["C0API"],
        mem_bytes=8 * 1024**3,
        vcpus=4,
        fs_capacity_bytes=128 * 1024**3,
        create_params={"_internal_runtime": "v2", "proxy_config": {"rules": [{"name": "api"}]}},
        snapshot_id="snap-1",
        snapshot_name="acme-monorepo",
        snapshot_status="ready",
        status_message="captured",
        snapshot_tag="latest",
        source_sandbox_id="sb-1",
        last_captured_at=now,
        refresh_status="success",
        refresh_kind="update",
        refresh_run_id="run-1",
        refresh_started_at=now,
        refresh_finished_at=now,
        refresh_log="+ make setup",
        refresh_error="a previous attempt timed out",
        refresh_cron_id="cron-1",
        refresh_steps=[
            RefreshStep(
                label="setup",
                status="success",
                started_at=now,
                finished_at=now,
                exit_code=0,
                log_path="/open-swe/environment/logs/setup.log",
            )
        ],
        refresh_sandbox_id="sb-builder",
        created_by="ramon",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.usefixtures("registry_db")
async def test_every_field_of_a_record_round_trips_through_its_row() -> None:
    record = _fully_populated(now_iso())
    defaults = Workspace(slug="base")
    assert [
        field
        for field in Workspace.model_fields
        if field != "slug" and getattr(record, field) == getattr(defaults, field)
    ] == []

    await WORKSPACES.put(record.slug, record)

    assert await WORKSPACES.get("base") == record


@pytest.mark.usefixtures("registry_db")
async def test_owner_of_repo_matches_however_the_repository_is_written() -> None:
    """The lookup goes through ``repository.key``, which GitHub casing cannot change."""
    await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["Acme/API"]), "alice")

    assert await WORKSPACES.owner_of_repo("acme/api") == "core"
    assert await WORKSPACES.owner_of_repo("ACME/API") == "core"
    assert await WORKSPACES.owner_of_repo("https://github.com/Acme/Api.git") == "core"
    assert await WORKSPACES.owner_of_repo("acme/other") is None
    # Routing asks about whatever an inbound event carried, so an unparseable
    # name reads as unowned rather than raising.
    assert await WORKSPACES.owner_of_repo("acme") is None


@pytest.mark.usefixtures("registry_db")
async def test_a_write_returns_the_repository_casing_it_stored() -> None:
    """``put`` answers with the stored view, so it agrees with ``get``."""
    async with postgres.session() as session:
        await Repository(full_name="acme/api").save(session)

    written = await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["ACME/API"]), "alice")

    assert written.repos == ["acme/api"]
    assert await WORKSPACES.get("core") == written


@pytest.mark.usefixtures("registry_db")
async def test_owner_of_slack_channel_normalizes_the_channel_id() -> None:
    await WORKSPACES.create(
        WorkspaceCreate(name="Core", repos=["acme/api"], slack_channel_ids=["C0API"]), "alice"
    )

    assert await WORKSPACES.owner_of_slack_channel(" c0api ") == "core"
    assert await WORKSPACES.owner_of_slack_channel("C0OTHER") is None
    assert await WORKSPACES.owner_of_slack_channel("") is None


@pytest.mark.usefixtures("registry_db")
async def test_an_update_releases_the_bindings_it_drops() -> None:
    await WORKSPACES.create(
        WorkspaceCreate(
            name="Core", repos=["acme/api", "acme/web"], slack_channel_ids=["C0API", "C0WEB"]
        ),
        "alice",
    )

    await WORKSPACES.apply_update(
        "core", WorkspaceUpdate(repos=["acme/web"], slack_channel_ids=["C0WEB"])
    )

    stored = await WORKSPACES.get("core")
    assert stored is not None
    assert stored.repos == ["acme/web"]
    assert stored.slack_channel_ids == ["C0WEB"]
    # What it let go of is free for another workspace to claim.
    released = await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/api"], slack_channel_ids=["C0API"]), "alice"
    )
    assert released.repos == ["acme/api"]
    assert await WORKSPACES.owner_of_slack_channel("C0API") == "oss"


@pytest.mark.usefixtures("registry_db")
async def test_a_binding_written_outside_the_store_is_respected() -> None:
    """The rows are the authority, not a listing a caller built earlier."""
    async with postgres.session() as session:
        repository = await Repository(full_name="acme/api").save(session)
        workspace = WorkspaceRow(slug="core", name="Core")
        session.add(workspace)
        await session.flush()
        session.add(WorkspaceRepositoryRow(repository_id=repository.id, workspace_id=workspace.id))

    with pytest.raises(ValueError, match="acme/api already belongs to workspace core"):
        await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["ACME/API"]), "alice")


@pytest.mark.usefixtures("registry_db")
async def test_a_claim_that_races_the_pre_check_still_names_the_owner() -> None:
    """The primary keys are the guarantee; the pre-check only makes it readable.

    ``put`` is where a create lands once its pre-check has passed, so writing
    through it is the claim that arrives while another one is in flight.
    """
    await WORKSPACES.create(
        WorkspaceCreate(name="Core", repos=["acme/api"], slack_channel_ids=["C0API"]), "alice"
    )

    with pytest.raises(ValueError, match="acme/api already belongs to workspace core"):
        await WORKSPACES.put("oss", Workspace(slug="oss", name="OSS", repos=["acme/api"]))
    with pytest.raises(ValueError, match="C0API already belongs to workspace core"):
        await WORKSPACES.put(
            "oss",
            Workspace(slug="oss", name="OSS", repos=["acme/oss"], slack_channel_ids=["C0API"]),
        )

    # Neither claim left a half-written workspace behind.
    assert await WORKSPACES.get("oss") is None
