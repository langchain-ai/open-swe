"""Pinned execution images and private sandbox authority."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.execution import ExecutionProfile
from openswe.sandboxes.lifecycle import SandboxCreateConfig


@pytest.fixture
def execution_defaults(monkeypatch: pytest.MonkeyPatch) -> ExecutionDefaults:
    defaults = ExecutionDefaults()
    monkeypatch.setattr(EXECUTION_DEFAULTS, "load", AsyncMock(return_value=defaults))
    return defaults


@pytest.mark.asyncio
async def test_global_defaults_are_pinned_and_public_metadata_cannot_expand_authority(
    monkeypatch, execution_defaults: ExecutionDefaults
) -> None:
    execution_defaults.snapshot_id = "global-1"
    execution_defaults.create_params = {"proxy_config": {"rules": [{"name": "original"}]}}
    profile = await ExecutionProfile.resolve()
    execution_defaults.snapshot_id = "global-2"
    execution_defaults.create_params = {"proxy_config": {"rules": [{"name": "changed"}]}}
    with ExecutionProfile.bind(profile):
        pinned = await SandboxCreateConfig.resolve()
        assert pinned.snapshot_id == "global-1"
        assert pinned.proxy_config == {"rules": [{"name": "original"}]}
        existing = await SandboxCreateConfig.for_thread(
            {
                "sandbox_id": "old",
                "sandbox_base_proxy_config": {"rules": ["injected"]},
                "sandbox_create_options": profile.create.model_dump(mode="json"),
            },
            thread_id="thread-1",
        )
        assert existing.proxy_config == {}
        assert existing.langsmith_options().extra_fields == {
            "preserve_memory_on_stop": True,
            "proxy_config": {},
        }
        assert (await existing.recreation_options()).snapshot_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["defaults", "base"])
async def test_mda_recreation_tracks_the_image_without_changing_bound_authority(
    monkeypatch, registry_db, execution_defaults: ExecutionDefaults, source: str
) -> None:
    from openswe.execution import SANDBOX_EXECUTION
    from openswe.sandboxes.managed import ManagedSandbox

    execution_defaults.create_params = {"proxy_config": {"rules": [{"name": "original"}]}}
    execution_defaults.snapshot_id = "snapshot-1"
    monkeypatch.setattr("openswe.threads.store.THREADS.update", AsyncMock())
    metadata = {"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}
    monkeypatch.setattr(
        "openswe.sandboxes.managed.get_sandbox_metadata", AsyncMock(return_value=metadata)
    )
    config = (
        SandboxCreateConfig(snapshot_id=None, create_params={"preserve_memory_on_stop": True})
        if source == "base"
        else await SandboxCreateConfig.resolve()
    )
    original = config.langsmith_options()
    await SANDBOX_EXECUTION.bind("thread-1", "mda-sandbox", config.authority_profile(), {})

    for snapshot in ("snapshot-2", "snapshot-3"):
        execution_defaults.snapshot_id = snapshot
        execution_defaults.create_params = {"proxy_config": {"rules": [{"name": "new"}]}}
        managed = await ManagedSandbox.for_thread("thread-1")
        assert managed is not None and managed.sandbox_id == "mda-sandbox"
        assert managed.create == original.model_copy(
            update={"snapshot_id": None if source == "base" else snapshot}
        )
        connected = await SandboxCreateConfig.for_thread(metadata, thread_id="thread-1")
        assert connected.langsmith_options() == original
        assert connected.proxy_config == original.extra_fields.get("proxy_config")


_HOST = "coordinator-1"
_WORKER = {"task_id": "00000000-0000-0000-0000-000000000001", "sandbox_host_thread_id": _HOST}
_UNBOUND = {"sandbox_id": None, "sandbox_base_proxy_config": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "host", "owner", "sandbox_id", "written"),
    [
        ({}, {}, "own", None, []),
        ({"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}, {}, "own", "mda-sandbox", []),
        # Files a Slack message brought wait for preparing to write them in the sandbox.
        (
            {"slack_files_pending": [{"url": "https://files.invalid/a", "path": "/tmp/a"}]},
            {},
            "own",
            None,
            [],
        ),
        # The sandbox the backend made becomes MDA's under its own name.
        (
            {"sandbox_id": "backend-sandbox"},
            {},
            "own",
            "backend-sandbox",
            [("thread-1", {"sandbox_owner": "mda"})],
        ),
        # The sandbox the backend made is gone: this run starts in MDA's instead of failing.
        ({"sandbox_id": "gone-sandbox"}, {}, "own", None, [("thread-1", _UNBOUND)]),
        # A sandbox of MDA's that is gone stays bound: nothing replaces it, a review's included.
        (
            {"sandbox_id": "gone-sandbox", "sandbox_owner": "mda"},
            {},
            "own",
            "gone-sandbox",
            [],
        ),
        # A worker shares the sandbox its coordinator is bound to now, whatever its own copy says.
        ({**_WORKER}, {"sandbox_id": "sandbox", "sandbox_owner": "mda"}, _HOST, "sandbox", []),
        (
            {**_WORKER, "sandbox_id": "stale"},
            {"sandbox_id": "sandbox", "sandbox_owner": "mda"},
            _HOST,
            "sandbox",
            [],
        ),
        # The coordinator's sandbox the backend made is adopted for the coordinator.
        (
            {**_WORKER, "sandbox_id": "sandbox"},
            {"sandbox_id": "sandbox"},
            _HOST,
            "sandbox",
            [(_HOST, {"sandbox_owner": "mda"})],
        ),
        # Before the coordinator has one, MDA shares the sandbox it opens for the coordinator.
        ({**_WORKER}, {}, _HOST, None, []),
    ],
)
async def test_every_run_gets_an_mda_sandbox(
    monkeypatch,
    execution_defaults: ExecutionDefaults,
    metadata: dict[str, object],
    host: dict[str, object],
    owner: str,
    sandbox_id: str | None,
    written: list[tuple[str, dict[str, object]]],
) -> None:
    """MDA creates new threads' sandboxes and adopts those the backend made; workers share
    their coordinator's.

    A run reports its sandbox behind its preparation only where preparing reaches none, and
    the deployment has not turned that off."""
    from openswe.sandboxes.lifecycle import TaskWorkerSandbox
    from openswe.sandboxes.managed import ManagedSandbox

    execution_defaults.snapshot_id = "snap-1"
    execution_defaults.create_params = {"proxy_config": {"rules": []}}

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return {**host} if thread_id == _HOST else metadata

    async def exists(sandbox_id: str) -> bool:
        return sandbox_id != "gone-sandbox"

    writes: list[tuple[str, dict[str, object]]] = []

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        writes.append((thread_id, metadata))

    for module in ("managed", "lifecycle"):
        monkeypatch.setattr(f"openswe.sandboxes.{module}.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.sandbox_exists", exists)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", write_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.THREADS.update", write_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.THREADS.search", AsyncMock(return_value=[]))
    monkeypatch.setattr(TaskWorkerSandbox, "host", AsyncMock(return_value=_HOST))

    sandbox = await ManagedSandbox.for_thread("thread-1", prepare_reaches_sandbox=False)

    assert writes == written
    assert sandbox.thread_id == (None if owner == "own" else owner)
    assert sandbox.sandbox_id == sandbox_id
    assert sandbox.bind_after_prepare is (owner == "own" and "slack_files_pending" not in metadata)
    # A review prepares in its sandbox, and the deployment can turn the late report off.
    review = await ManagedSandbox.for_thread("thread-1")
    monkeypatch.setenv("MDA_BIND_SANDBOX_AFTER_PREPARE", "false")
    disabled = await ManagedSandbox.for_thread("thread-1", prepare_reaches_sandbox=False)
    assert not review.bind_after_prepare
    assert not disabled.bind_after_prepare
    assert sandbox.create.snapshot_id == (None if sandbox_id is not None else "snap-1")


@pytest.mark.asyncio
async def test_mda_adopts_a_backend_sandbox_only_one_thread_is_bound_to(
    monkeypatch, registry_db
) -> None:
    """MDA keeps one sandbox per thread, so it adopts a sandbox the backend made only where no
    other thread but the task workers sharing it is bound to it; elsewhere the thread starts in
    a fresh sandbox, and the threads still bound to the shared one keep it."""
    from openswe.sandboxes.lifecycle import BackendSandbox
    from openswe.threads import index
    from openswe.threads.store import THREADS

    monkeypatch.setattr(THREADS.copies, "start", MagicMock())
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", THREADS.update)
    monkeypatch.setattr(
        "openswe.sandboxes.providers.langsmith.sandbox_exists", AsyncMock(return_value=True)
    )
    for thread_id, metadata in (
        ("coordinator", {"sandbox_id": "own-box"}),
        ("worker", {**_WORKER, "sandbox_host_thread_id": "coordinator", "sandbox_id": "own-box"}),
        ("first", {"sandbox_id": "shared-box"}),
        ("second", {"sandbox_id": "shared-box"}),
    ):
        await index.create(thread_id, runtime="mda", metadata=metadata)

    assert await BackendSandbox("coordinator", "own-box").hand_to_mda() == "own-box"
    assert await BackendSandbox("first", "shared-box").hand_to_mda() is None

    threads = await THREADS.get_many(["coordinator", "worker", "first", "second"])
    assert {
        thread_id: (thread.metadata.get("sandbox_id"), thread.metadata.get("sandbox_owner"))
        for thread_id, thread in threads.items()
    } == {
        "coordinator": ("own-box", "mda"),
        "worker": ("own-box", None),
        "first": (None, None),
        "second": ("shared-box", None),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "reported", "update"),
    [
        (
            {},
            "mda-sandbox",
            {
                "sandbox_owner": "mda",
                "sandbox_id": "mda-sandbox",
                "sandbox_base_proxy_config": {"rules": []},
            },
        ),
        # MDA could not open the sandbox: the thread stays MDA's, and binding reports why.
        ({}, None, {"sandbox_owner": "mda"}),
        ({"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}, "mda-sandbox", None),
        ({"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}, None, None),
    ],
)
async def test_the_backend_binds_the_sandbox_mda_reports(
    monkeypatch,
    registry_db,
    execution_defaults: ExecutionDefaults,
    metadata: dict[str, object],
    reported: str | None,
    update: dict[str, object] | None,
) -> None:
    from openswe.sandboxes.lifecycle import bind_mda_sandbox, ensure_sandbox_for_thread
    from openswe.sandboxes.state import SandboxUnreachableError

    execution_defaults.create_params = {"proxy_config": {"rules": []}}
    updates: list[dict[str, object]] = []

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return {**metadata, **(updates[-1] if updates else {})}

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        updates.append(metadata)

    monkeypatch.setattr("openswe.sandboxes.lifecycle.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.threads.store.THREADS.update", write_metadata)
    monkeypatch.setattr("openswe.execution_images.EXECUTION_IMAGES.maybe_start_update", AsyncMock())

    profile = await ExecutionProfile.resolve()
    if reported is None and "sandbox_id" not in metadata:
        with pytest.raises(SandboxUnreachableError, match="could not open.*HTTP 503") as raised:
            await bind_mda_sandbox("thread-1", reported, open_error="SandboxClientError: HTTP 503")
        assert raised.value.reason == "SandboxClientError: HTTP 503"
        with pytest.raises(SandboxUnreachableError, match="could not open"):
            await ensure_sandbox_for_thread("thread-1")
    else:
        with ExecutionProfile.bind(profile):
            await bind_mda_sandbox("thread-1", reported)

    assert updates == ([] if update is None else [update])


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_a_bound_sandbox_langsmith_deleted_ends_the_run(monkeypatch, deleted: bool) -> None:
    """MDA could not open the thread's sandbox: the thread keeps its binding, and the run ends
    only where LangSmith confirms the sandbox deleted, since nothing replaces it."""
    from openswe.sandboxes.lifecycle import bind_mda_sandbox
    from openswe.sandboxes.state import SandboxDeletedError

    metadata = {"sandbox_id": "original", "sandbox_owner": "mda"}
    updates = AsyncMock()
    monkeypatch.setattr(
        "openswe.sandboxes.lifecycle.get_sandbox_metadata", AsyncMock(return_value=metadata)
    )
    monkeypatch.setattr("openswe.threads.store.THREADS.update", updates)
    monkeypatch.setattr(
        "openswe.sandboxes.providers.langsmith.sandbox_exists",
        AsyncMock(return_value=not deleted),
    )
    error = "BoundSandboxMissingError: The thread's bound sandbox 'original' no longer exists"

    if deleted:
        with pytest.raises(SandboxDeletedError) as raised:
            await bind_mda_sandbox("thread-1", None, open_error=error)
        assert (raised.value.sandbox_id, raised.value.reason) == ("original", error)
    else:
        await bind_mda_sandbox("thread-1", None, open_error=error)
    updates.assert_not_awaited()


@pytest.mark.asyncio
async def test_replicas_cannot_rebind_a_thread_to_a_different_sandbox(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from openswe.sandboxes.lifecycle import bind_mda_sandbox
    from openswe.sandboxes.state import SANDBOX_CONNECTIONS, SandboxUnreachableError

    metadata: dict[str, object] = {"sandbox_id": "original", "sandbox_owner": "mda"}
    updates: list[dict[str, object]] = []
    connection = MagicMock()
    monkeypatch.setitem(SANDBOX_CONNECTIONS, "original", connection)

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return metadata

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        updates.append(metadata)

    monkeypatch.setattr("openswe.sandboxes.lifecycle.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", write_metadata)

    for reported in ("original", "original-c9eec961", "original", "original-c9eec961"):
        if reported == "original":
            await bind_mda_sandbox("thread-1", reported)
        else:
            with pytest.raises(
                SandboxUnreachableError, match="Expected original; opened original-"
            ):
                await bind_mda_sandbox("thread-1", reported)
        assert metadata["sandbox_id"] == "original"
        assert SANDBOX_CONNECTIONS["original"] is connection
    assert updates == []


class TestLangSmithSandboxCredentials:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("linked_github", [False, True])
    async def test_cloud_lifecycle_never_borrows_github_credentials(
        self,
        monkeypatch,
        registry_db,
        execution_defaults: ExecutionDefaults,
        linked_github: bool,
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from uuid import UUID

        from openswe.sandboxes import lifecycle
        from openswe.sandboxes.managed import ManagedSandbox
        from openswe.sandboxes.providers.langsmith import AdoptedSandbox, LiveSandboxProxy
        from openswe.users.models import User, UserIdentity

        owner = User(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            identities=[UserIdentity(provider="langsmith", external_id="ls-user")],
        )
        if linked_github:
            owner.identities.append(
                UserIdentity(provider="github", external_id="123", login="alice")
            )
        tag_id = "00000000-0000-0000-0000-000000000002"
        cloud_rule = {
            "name": "aws",
            "type": "aws",
            "aws": {
                "role_arn": "arn:aws:iam::123456789012:role/ReadOnly",
                "subject_scope": f"tag:{tag_id}",
            },
        }
        base_config = {"rules": [{"name": "github", "headers": [{"value": "stale"}]}, cloud_rule]}
        execution_defaults.snapshot_id = "snap-1"
        execution_defaults.create_params = {"proxy_config": base_config, "tag_value_ids": [tag_id]}
        metadata = {
            "owner_type": "user",
            "owner_user_id": str(owner.id),
            "owner_login": f"user:{owner.id}",
        }
        backend = MagicMock(id="sandbox-ls")
        mint = AsyncMock(side_effect=AssertionError("GitHub token minting is forbidden"))
        scope = AsyncMock(side_effect=AssertionError("GitHub scope lookup is forbidden"))
        monkeypatch.setattr(User, "get", AsyncMock(return_value=owner))
        monkeypatch.setattr(lifecycle, "get_sandbox_metadata", AsyncMock(return_value=metadata))
        monkeypatch.setattr(
            "openswe.sandboxes.managed.get_sandbox_metadata", AsyncMock(return_value=metadata)
        )
        monkeypatch.setattr(lifecycle, "sandbox_token", mint)
        monkeypatch.setattr(lifecycle, "thread_token_repositories", scope)
        monkeypatch.setattr(lifecycle, "configure_git_identity", AsyncMock())
        monkeypatch.setattr(lifecycle, "connect_existing_sandbox", AsyncMock(return_value=backend))
        monkeypatch.setattr(lifecycle, "set_sandbox_backend", lambda thread_id, backend: backend)
        monkeypatch.setattr(
            "openswe.execution_images.EXECUTION_IMAGES.maybe_start_update", AsyncMock()
        )
        monkeypatch.setattr("openswe.threads.store.THREADS.update", AsyncMock())
        adopted = AdoptedSandbox(rules=[cloud_rule], env_vars={})
        monkeypatch.setattr(LiveSandboxProxy, "adopt", AsyncMock(return_value=adopted))

        managed = await ManagedSandbox.for_thread("ls-thread")
        assert managed.create.snapshot_id == "snap-1"
        # MDA creates it with the workspace's cloud identity, and no GitHub credentials.
        assert managed.proxy_config is not None
        assert managed.proxy_config["rules"] == [cloud_rule]
        await lifecycle.bind_mda_sandbox("ls-thread", "sandbox-ls")
        metadata.update(
            sandbox_id="sandbox-ls", sandbox_owner="mda", sandbox_base_proxy_config=base_config
        )
        await lifecycle.ensure_sandbox_for_thread("ls-thread")
        replanned = await ManagedSandbox.for_thread("ls-thread")
        assert replanned.proxy_config is not None
        assert replanned.proxy_config["rules"] == [cloud_rule]
        mint.assert_not_awaited()
        scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_run_digest_is_frozen_and_rejects_modified_records(monkeypatch) -> None:
    import json

    from openswe import execution

    record = json.loads(
        '{"captured_at":null,"create":{"delete_after_stop_seconds":400,"extra_fields":{"proxy_config":{"rules":[]}},"fs_capacity_bytes":100,"idle_ttl_seconds":300,"mem_bytes":200,"snapshot_id":"old-snapshot","vcpus":2},"instructions":"Old instructions","name":"Historical","refresh_enabled":false,"refresh_workspace":"deleted-environment","replacement":null,"repos":["acme/api"],"uses_defaults":false}'
    )
    digest = "7975e5b0861291bee270bce69c31d0d2b80c1a1cef26ba0823153c70558de4cc"
    load = AsyncMock(return_value=record)
    monkeypatch.setattr(execution._records, "get", load)
    kept = await ExecutionProfile.kept(digest)
    assert kept.create.snapshot_id == "old-snapshot"
    assert kept.digest() == digest
    config = await SandboxCreateConfig.from_profile(kept)
    assert config.proxy_config == {"rules": []}
    record["create"]["extra_fields"] = {"proxy_config": {"rules": ["injected"]}}
    with pytest.raises(ValueError, match="profile has changed"):
        await ExecutionProfile.kept(digest)


@pytest.mark.asyncio
async def test_external_sandbox_skips_credentials_and_proxy(monkeypatch) -> None:
    from openswe.agent_api.run_sandbox import RunSandbox
    from openswe.sandboxes.lifecycle import SandboxCredentialPolicy
    from openswe.sandboxes.managed import ManagedSandbox
    from openswe.tasks.schemas import ThreadMetadata

    metadata: dict[str, object] = {
        "owner_type": "system",
        "sandbox_id": "tb-task-1",
        "sandbox_owner": "external",
        "sandbox_work_dir": "/app",
    }
    monkeypatch.setattr(
        "openswe.sandboxes.managed.get_sandbox_metadata", AsyncMock(return_value=metadata)
    )
    monkeypatch.setattr(
        "openswe.sandboxes.lifecycle.get_sandbox_metadata", AsyncMock(return_value=metadata)
    )
    managed = await ManagedSandbox.for_thread("thread-ext")
    assert managed.sandbox_id == "tb-task-1"
    assert managed.external is True
    # Nothing writes an attached sandbox's proxy.
    spec = await RunSandbox({"configurable": {"thread_id": "thread-ext"}}, reviews=False).spec()
    assert spec.proxy_config is None
    assert not await SandboxCredentialPolicy(
        ThreadMetadata.model_validate(metadata)
    ).allows_github()
    worker_metadata: dict[str, object] = {
        "owner_type": "system",
        "sandbox_id": "tb-task-1",
        "sandbox_host_thread_id": "thread-ext",
        "task_id": "01950000-0000-7000-8000-000000000001",
    }
    by_thread = {"thread-ext": metadata, "thread-worker": worker_metadata}
    monkeypatch.setattr(
        "openswe.sandboxes.lifecycle.get_sandbox_metadata",
        AsyncMock(side_effect=lambda tid: by_thread[tid]),
    )
    assert not await SandboxCredentialPolicy(
        ThreadMetadata.model_validate(worker_metadata)
    ).allows_github()
