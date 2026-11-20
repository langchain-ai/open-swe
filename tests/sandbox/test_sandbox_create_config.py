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
    monkeypatch.setenv("SANDBOX_CREATE_EXTRA_JSON", '{"proxy_config":{"rules":["injected"]}}')
    with ExecutionProfile.bind(profile):
        pinned = await SandboxCreateConfig.resolve()
        assert pinned.snapshot_id == "global-1"
        assert pinned.proxy_config == {"rules": [{"name": "original"}]}
        assert (await SandboxCreateConfig.resolve(source="base")).snapshot_id is None
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
    config = await SandboxCreateConfig.resolve(source=source)
    original = config.langsmith_options()
    await SANDBOX_EXECUTION.bind("thread-1", "mda-sandbox", config.authority_profile(), {})

    for snapshot in ("snapshot-2", "snapshot-3"):
        execution_defaults.snapshot_id = snapshot
        execution_defaults.create_params = {"proxy_config": {"rules": [{"name": "new"}]}}
        monkeypatch.setenv("SANDBOX_CREATE_EXTRA_JSON", '{"proxy_config":{"rules":["injected"]}}')
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
        # A thread handed over by ``recreate_sandbox`` gets the sandbox it asked for, once.
        (
            {"sandbox_replacement": {"workspace": "default", "source": "base"}},
            "mda-sandbox",
            {
                "sandbox_owner": "mda",
                "sandbox_id": "mda-sandbox",
                "sandbox_replacement": None,
                "sandbox_base_proxy_config": {},
            },
        ),
        (
            {
                "sandbox_id": "mda-sandbox",
                "sandbox_owner": "mda",
                "sandbox_replacement": {"workspace": "default", "source": "base"},
            },
            "mda-sandbox",
            None,
        ),
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
    from openswe.sandboxes.lifecycle import (
        bind_mda_sandbox,
        ensure_sandbox_for_thread,
        request_mda_replacement,
    )
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

    if "sandbox_replacement" in metadata and "sandbox_id" not in metadata:
        with pytest.raises(ValueError, match="not privately pinned"):
            await bind_mda_sandbox("thread-1", reported)
        await request_mda_replacement("thread-1", source="base", invocation_id="request-1")
        updates.clear()

    profile = await ExecutionProfile.resolve(thread_id="thread-1")
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
@pytest.mark.parametrize("replacement_pending", [False, True])
async def test_replicas_cannot_rebind_a_thread_to_a_different_sandbox(
    monkeypatch, replacement_pending: bool
) -> None:
    from unittest.mock import MagicMock

    from openswe.sandboxes.lifecycle import bind_mda_sandbox
    from openswe.sandboxes.state import SANDBOX_CONNECTIONS, SandboxUnreachableError

    metadata: dict[str, object] = {"sandbox_id": "original", "sandbox_owner": "mda"}
    if replacement_pending:
        metadata["sandbox_replacement"] = {"workspace": "default", "source": "base"}
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


@pytest.mark.asyncio
async def test_explicit_replacement_privately_pins_global_authority(
    monkeypatch,
    registry_db,
) -> None:
    from openswe.execution import SANDBOX_EXECUTION
    from openswe.sandboxes.lifecycle import reconnect_replaced_sandbox, request_mda_replacement

    defaults = ExecutionDefaults(create_params={"proxy_config": {"rules": ["other"]}})
    monkeypatch.setattr(EXECUTION_DEFAULTS, "load", AsyncMock(return_value=defaults))
    thread_metadata: dict[str, object] = {
        "sandbox_id": "mda-sandbox",
        "sandbox_owner": "mda",
        "sandbox_base_proxy_config": {"rules": ["original"]},
    }
    updates: list[dict[str, object]] = []
    publication_failures: list[Exception] = [RuntimeError("metadata unavailable")]

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return thread_metadata

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        if publication_failures:
            raise publication_failures.pop()
        updates.append(metadata)
        thread_metadata.update(metadata)

    ensure = AsyncMock()
    monkeypatch.setattr("openswe.sandboxes.lifecycle.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.threads.store.THREADS.update", write_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.ensure_sandbox_for_thread", ensure)
    monkeypatch.setattr("openswe.execution_images.EXECUTION_IMAGES.maybe_start_update", AsyncMock())

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await request_mda_replacement("thread-1", source="defaults", invocation_id="request-1")
    requested = await request_mda_replacement(
        "thread-1", source="defaults", invocation_id="request-1"
    )
    assert (
        await request_mda_replacement("thread-1", source="defaults", invocation_id="request-1")
        == requested
    )
    queued = await ExecutionProfile.resolve(thread_id="thread-1")
    defaults.create_params["proxy_config"] = {"rules": ["changed"]}
    with pytest.raises(ValueError, match="different replacement requires a new run"):
        await request_mda_replacement("thread-1", source="defaults", invocation_id="request-1")
    with pytest.raises(ValueError, match="has not completed"):
        await request_mda_replacement("thread-1", source="defaults", invocation_id="request-2")
    existing = await SandboxCreateConfig.for_thread(thread_metadata, thread_id="thread-1")
    assert existing.proxy_config == {}
    with pytest.raises(ValueError, match="does not own"):
        await reconnect_replaced_sandbox("thread-1", invocation_id="request-2")
    publication_failures.append(RuntimeError("metadata unavailable"))
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await reconnect_replaced_sandbox("thread-1", invocation_id="request-1")
    with pytest.raises(ValueError, match="has not completed"):
        await request_mda_replacement("thread-1", source="defaults", invocation_id="request-2")
    await reconnect_replaced_sandbox("thread-1", invocation_id="request-1")
    assert updates[-1] == {
        "sandbox_base_proxy_config": {"rules": ["other"]},
        "sandbox_replacement": None,
    }
    bound = await SANDBOX_EXECUTION.bound("thread-1", "mda-sandbox")
    assert bound is not None and bound.create == requested
    publications = len(updates)
    await SANDBOX_EXECUTION.bind(
        "thread-1",
        "mda-sandbox",
        bound,
        {"sandbox_base_proxy_config": {"rules": ["stale-duplicate"]}},
        replacement=queued.replacement,
    )
    assert len(updates) == publications
    assert thread_metadata["sandbox_base_proxy_config"] == {"rules": ["other"]}
    with pytest.raises(ValueError, match="completed sandbox replacement has different authority"):
        await SANDBOX_EXECUTION.bind(
            "thread-1", "different-sandbox", bound, {}, replacement=queued.replacement
        )
    thread_metadata["sandbox_base_proxy_config"] = {"rules": ["injected"]}
    thread_metadata["sandbox_create_options"] = {"extra_fields": {"proxy_config": "injected"}}
    existing = await SandboxCreateConfig.for_thread(thread_metadata, thread_id="thread-1")
    assert existing.proxy_config == {"rules": ["other"]}
    defaults.create_params["proxy_config"] = {"rules": ["other"]}
    await request_mda_replacement("thread-1", source="defaults", invocation_id="request-2")
    with pytest.raises(ValueError, match="does not own"):
        await reconnect_replaced_sandbox("thread-1", invocation_id="request-1")
    with ExecutionProfile.bind(queued), pytest.raises(ValueError, match="changed; start a new run"):
        await SandboxCreateConfig.for_thread({}, thread_id="thread-1")
    ensure.assert_awaited_once_with(
        "thread-1",
        github_proxy_repositories=None,
        github_proxy_permissions=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["error", "interrupted"])
async def test_failed_replacement_can_be_retried_without_accepting_stale_callbacks(
    monkeypatch, registry_db, execution_defaults: ExecutionDefaults, status: str
) -> None:
    import asyncio
    from datetime import UTC, datetime

    from openswe.execution import SANDBOX_EXECUTION
    from openswe.run_config import RunConfig
    from openswe.sandboxes.lifecycle import reconnect_replaced_sandbox, request_mda_replacement
    from openswe.threads.runtimes import RUNTIMES, MdaRun, MdaRuntime

    metadata: dict[str, object] = {"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}

    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        lambda: RunConfig(invocation_id="invocation-1", run_id="runtime-1"),
    )
    monkeypatch.setattr("openswe.threads.store.THREADS.update", AsyncMock())
    monkeypatch.setattr(
        "openswe.sandboxes.lifecycle.get_sandbox_metadata", AsyncMock(return_value=metadata)
    )
    monkeypatch.setattr("openswe.sandboxes.lifecycle.ensure_sandbox_for_thread", AsyncMock())
    runtime_run = AsyncMock()
    runtime = MdaRuntime("https://mda.test")
    monkeypatch.setattr(runtime, "run", runtime_run)
    monkeypatch.setattr(type(RUNTIMES), "mda_runtime", property(lambda _: runtime))
    await request_mda_replacement("thread-1", source="base")
    pending = await SANDBOX_EXECUTION.pending("thread-1")
    assert pending is not None and pending.run_id == "runtime-1"

    for owner_status in ("pending", "running", None):
        runtime_run.return_value = (
            MdaRun(run_id="runtime-1", status=owner_status, created_at=datetime.now(UTC))
            if owner_status is not None
            else None
        )
        with pytest.raises(ValueError, match="has not completed"):
            await request_mda_replacement(
                "thread-1",
                source="base",
                invocation_id="invocation-2",
                run_id="runtime-2",
            )
    runtime_run.side_effect = RuntimeError("runtime unavailable")
    with pytest.raises(RuntimeError, match="runtime unavailable"):
        await request_mda_replacement(
            "thread-1",
            source="base",
            invocation_id="invocation-2",
            run_id="runtime-2",
        )
    assert await SANDBOX_EXECUTION.pending("thread-1") == pending
    runtime_run.side_effect = None
    runtime_run.return_value = MdaRun.model_validate(
        {"run_id": "runtime-1", "status": status, "created_at": datetime.now(UTC)}
    )
    requested = await request_mda_replacement(
        "thread-1",
        source="base",
        invocation_id="invocation-2",
        run_id="runtime-2",
    )
    runtime_run.assert_awaited_with("thread-1", "runtime-1")
    pending = await SANDBOX_EXECUTION.pending("thread-1")
    assert pending is not None and pending.invocation_id == "invocation-2"
    with pytest.raises(ValueError, match="does not own"):
        await reconnect_replaced_sandbox("thread-1", invocation_id="invocation-1")
    await reconnect_replaced_sandbox("thread-1", invocation_id="invocation-2")
    assert await SANDBOX_EXECUTION.pending("thread-1") is None
    bound = await SANDBOX_EXECUTION.bound("thread-1", "mda-sandbox")
    assert bound is not None and bound.create == requested

    await SANDBOX_EXECUTION.request("thread-1", bound, "invocation-3", {}, run_id="runtime-3")
    contenders = asyncio.Barrier(2)

    async def finished_run(thread_id: str, run_id: str) -> MdaRun:
        assert (thread_id, run_id) == ("thread-1", "runtime-3")
        await contenders.wait()
        return MdaRun(run_id=run_id, status="error", created_at=datetime.now(UTC))

    runtime_run.side_effect = finished_run
    results = await asyncio.gather(
        *(
            SANDBOX_EXECUTION.request(
                "thread-1", bound, f"invocation-{index}", {}, run_id=f"runtime-{index}"
            )
            for index in (4, 5)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    pending = await SANDBOX_EXECUTION.pending("thread-1")
    assert pending is not None and pending.pin() in results


class TestLangSmithSandboxCredentials:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("linked_github", [False, True])
    @pytest.mark.parametrize("mda_writes", [False, True])
    async def test_cloud_lifecycle_never_borrows_github_credentials(
        self,
        monkeypatch,
        registry_db,
        execution_defaults: ExecutionDefaults,
        linked_github: bool,
        mda_writes: bool,
    ) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from uuid import UUID

        from openswe.sandboxes import lifecycle
        from openswe.sandboxes.managed import ManagedSandbox
        from openswe.sandboxes.providers.langsmith import sandbox_proxy_config
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
        tools_rule = {"name": "open-swe-thread-tools"}
        backend = MagicMock(id="sandbox-ls")
        mint = AsyncMock(side_effect=AssertionError("GitHub token minting is forbidden"))
        scope = AsyncMock(side_effect=AssertionError("GitHub scope lookup is forbidden"))
        configure = AsyncMock(side_effect=sandbox_proxy_config)
        if mda_writes:
            monkeypatch.setenv("MDA_WRITES_SANDBOX_PROXY", "true")
        else:
            monkeypatch.delenv("MDA_WRITES_SANDBOX_PROXY", raising=False)
        monkeypatch.setattr(User, "get", AsyncMock(return_value=owner))
        monkeypatch.setattr(lifecycle, "get_sandbox_metadata", AsyncMock(return_value=metadata))
        monkeypatch.setattr(
            "openswe.sandboxes.managed.get_sandbox_metadata", AsyncMock(return_value=metadata)
        )
        monkeypatch.setattr(lifecycle, "sandbox_token", mint)
        monkeypatch.setattr(lifecycle, "thread_token_repositories", scope)
        monkeypatch.setattr(lifecycle, "configure_sandbox_proxy", configure)
        monkeypatch.setattr(lifecycle, "configure_git_identity", AsyncMock())
        monkeypatch.setattr(lifecycle, "connect_existing_sandbox", AsyncMock(return_value=backend))
        monkeypatch.setattr(lifecycle, "set_sandbox_backend", lambda thread_id, backend: backend)
        monkeypatch.setattr(
            "openswe.execution_images.EXECUTION_IMAGES.maybe_start_update", AsyncMock()
        )
        monkeypatch.setattr("openswe.threads.store.THREADS.update", AsyncMock())
        monkeypatch.setattr(
            "openswe.sandboxes.tool_access.tool_proxy_rule", AsyncMock(return_value=tools_rule)
        )

        managed = await ManagedSandbox.for_thread("ls-thread")
        assert managed.writes_proxy is mda_writes
        assert managed.create.snapshot_id == "snap-1"
        await lifecycle.bind_mda_sandbox("ls-thread", "sandbox-ls")
        metadata.update(
            sandbox_id="sandbox-ls", sandbox_owner="mda", sandbox_base_proxy_config=base_config
        )
        await lifecycle.reconnect_replaced_sandbox("ls-thread")
        assert await lifecycle.refresh_thread_github_proxy("ls-thread") is None
        if mda_writes:
            # MDA writes it as the backend hands it over, with no GitHub rules.
            assert configure.await_count == 0
            config, expires_at = await lifecycle.mda_proxy_config(
                "ls-thread", "sandbox-ls", github_scope=(None, None)
            )
            assert config["rules"] == [tools_rule, cloud_rule]
            assert expires_at is None
        else:
            assert configure.await_count == 2
            for call in configure.await_args_list:
                assert call.args == ("sandbox-ls", None)
                clean_config = await sandbox_proxy_config(*call.args, **call.kwargs)
                assert clean_config["rules"] == [tools_rule, cloud_rule]
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
async def test_external_sandbox_skips_credentials_proxy_and_recreation(monkeypatch) -> None:
    import importlib

    from openswe.sandboxes.lifecycle import SandboxCredentialPolicy, refresh_thread_github_proxy
    from openswe.sandboxes.managed import ManagedSandbox
    from openswe.tasks.schemas import ThreadMetadata
    from openswe.tools.recreate_sandbox import recreate_sandbox

    recreate_mod = importlib.import_module("openswe.tools.recreate_sandbox")
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
    monkeypatch.setattr(recreate_mod, "get_sandbox_metadata", AsyncMock(return_value=metadata))
    configure_proxy = AsyncMock()
    monkeypatch.setattr("openswe.sandboxes.lifecycle.configure_sandbox_proxy", configure_proxy)

    monkeypatch.setenv("MDA_WRITES_SANDBOX_PROXY", "true")
    managed = await ManagedSandbox.for_thread("thread-ext")
    assert managed.sandbox_id == "tb-task-1"
    assert managed.external is True
    # Nothing writes an attached sandbox's proxy, MDA included.
    assert not managed.writes_proxy
    assert not await SandboxCredentialPolicy(
        ThreadMetadata.model_validate(metadata)
    ).allows_github()
    assert await refresh_thread_github_proxy("thread-ext") is None
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
    monkeypatch.setattr(
        "openswe.sandboxes.lifecycle.SandboxCreateConfig.bound_profile",
        AsyncMock(return_value=None),
    )
    assert not await SandboxCredentialPolicy(
        ThreadMetadata.model_validate(worker_metadata)
    ).allows_github()
    assert await refresh_thread_github_proxy("thread-worker") is None
    configure_proxy.assert_not_awaited()

    monkeypatch.setattr(
        "openswe.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-ext"}}
    )
    recreated = await recreate_sandbox()
    assert recreated["success"] is False
