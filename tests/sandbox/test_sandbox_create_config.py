"""Which workspace image a new sandbox boots from."""

from unittest.mock import AsyncMock

import pytest

from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.execution import ExecutionProfile
from openswe.sandboxes.lifecycle import SandboxCreateConfig
from openswe.workspaces.store import Workspace


@pytest.fixture
def execution_defaults(monkeypatch: pytest.MonkeyPatch) -> ExecutionDefaults:
    defaults = ExecutionDefaults()
    monkeypatch.setattr(EXECUTION_DEFAULTS, "load", AsyncMock(return_value=defaults))
    return defaults


@pytest.mark.asyncio
async def test_inherited_sandbox_tracks_default_without_changing_workspace_identity(
    monkeypatch,
    execution_defaults: ExecutionDefaults,
    registry_db,
) -> None:
    child_params = {
        "proxy_config": {"rules": [{"name": "child-api", "match_hosts": ["child.example.com"]}]},
        "tag_value_ids": ["00000000-0000-0000-0000-000000000001"],
    }
    child = Workspace(
        slug="child",
        inherit_default_sandbox=True,
        prompt="Child instructions",
        create_params=child_params,
    )
    default = Workspace(
        slug="default",
        update_script="git pull",
        snapshot_status="ready",
        snapshot_id="snap-1",
        mem_bytes=1234,
        create_params={
            "preserve_memory_on_stop": False,
            "proxy_config": {
                "rules": [
                    {
                        "name": "aws",
                        "type": "aws",
                        "aws": {"role_arn": "arn:aws:iam::123456789012:role/DefaultOnly"},
                    }
                ]
            },
            "env_vars": {"DEFAULT_SETTING": "default"},
            "mount_config": {"mounts": []},
        },
    )

    async def load(slug: str | None) -> Workspace:
        return child if slug == "child" else default

    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    config = await SandboxCreateConfig.resolve("child")
    assert config.snapshot_id == "snap-1"
    assert config.resources == {"mem_bytes": 1234}
    assert config.create_params == {**child_params, "preserve_memory_on_stop": True}
    assert default.create_params["preserve_memory_on_stop"] is False
    assert child.prompt == "Child instructions"
    default.snapshot_id = "snap-2"
    assert (await SandboxCreateConfig.resolve("child")).snapshot_id == "snap-2"
    child.inherit_default_sandbox = False
    assert (await SandboxCreateConfig.resolve("child")).snapshot_id is None
    assert (await SandboxCreateConfig.resolve("child", source="base")).snapshot_id is None

    legacy_options = (await SandboxCreateConfig.resolve("child")).langsmith_options()
    execution_defaults.enabled = True
    execution_defaults.snapshot_id = "global-1"
    execution_defaults.create_params = {"proxy_config": {"rules": [{"name": "global"}]}}
    profile = await ExecutionProfile.resolve("child")
    execution_defaults.snapshot_id = "global-2"
    execution_defaults.create_params = {"proxy_config": {"rules": [{"name": "changed"}]}}
    with ExecutionProfile.bind(profile):
        pinned = await SandboxCreateConfig.resolve("child")
        assert pinned.langsmith_options().snapshot_id == "global-1"
        assert pinned.proxy_config == {"rules": [{"name": "global"}]}
        assert (await SandboxCreateConfig.resolve("child", source="base")).snapshot_id is None
        existing = await SandboxCreateConfig.for_thread(
            {
                "sandbox_id": "old",
                "sandbox_base_proxy_config": child_params["proxy_config"],
                "sandbox_create_options": profile.create.model_dump(mode="json"),
                "sandbox_replacement": {"workspace": "default", "source": "workspace"},
            },
            "child",
            thread_id="thread-1",
        )
        assert existing.langsmith_options() == legacy_options
    with ExecutionProfile.bind(None):
        assert (await SandboxCreateConfig.resolve("child")).snapshot_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("source", ["workspace", "defaults", "base"])
async def test_mda_recreation_tracks_the_source_image_without_changing_bound_authority(
    monkeypatch, registry_db, execution_defaults: ExecutionDefaults, enabled: bool, source: str
) -> None:
    from openswe.execution import SANDBOX_EXECUTION
    from openswe.sandboxes.managed import ManagedSandbox

    original_params = {"proxy_config": {"rules": [{"name": "original-cloud-identity"}]}}
    workspace = Workspace(
        slug="source",
        snapshot_status="ready",
        snapshot_id="snapshot-1",
        create_params=original_params,
    )
    other = Workspace(slug="other", snapshot_status="ready", snapshot_id="unrelated-image")
    execution_defaults.create_params = original_params
    execution_defaults.snapshot_id = "snapshot-1"
    execution_defaults.enabled = enabled

    async def load(slug: str | None) -> Workspace:
        return workspace if slug == "source" else other

    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.threads.store.THREADS.update", AsyncMock())
    metadata = {"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}
    monkeypatch.setattr(
        "openswe.sandboxes.managed.get_sandbox_metadata", AsyncMock(return_value=metadata)
    )
    if source == "defaults":
        config = SandboxCreateConfig.from_defaults(execution_defaults)
    elif source == "base":
        config = await SandboxCreateConfig.resolve(source="base")
    else:
        config = await SandboxCreateConfig.legacy("source")
    original = config.langsmith_options()
    await SANDBOX_EXECUTION.bind("thread-1", "mda-sandbox", config.authority_profile(), {})

    for snapshot in ("snapshot-2", "snapshot-3"):
        workspace.snapshot_id = execution_defaults.snapshot_id = snapshot
        changed_params = {"proxy_config": {"rules": [{"name": "new-cloud-identity"}]}}
        workspace.create_params = execution_defaults.create_params = changed_params
        managed = await ManagedSandbox.for_thread("thread-1", "other", allow_github=False)
        assert managed is not None and managed.sandbox_id == "mda-sandbox"
        assert managed.create == original.model_copy(
            update={"snapshot_id": None if source == "base" else snapshot}
        )
        connected = await SandboxCreateConfig.for_thread(metadata, "other", thread_id="thread-1")
        assert connected.langsmith_options() == original


_HOST = "coordinator-1"
_WORKER = {"task_id": "00000000-0000-0000-0000-000000000001", "sandbox_host_thread_id": _HOST}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "host", "owner"),
    [
        ({}, {}, "own"),
        # Moved off a Mac: connecting to its new sandbox carries the checkout over from GitHub.
        ({"sandbox_handoff_from": "bridge:mac"}, {}, "own"),
        ({"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}, {}, "own"),
        # Files a Slack message brought wait for preparing to write them in the sandbox.
        (
            {"slack_files_pending": [{"url": "https://files.invalid/a", "path": "/tmp/a"}]},
            {},
            "own",
        ),
        ({"sandbox_id": "backend-sandbox"}, {}, None),
        # The sandbox the backend made is gone: this run starts in MDA's instead of failing.
        ({"sandbox_id": "gone-sandbox"}, {}, "own"),
        ({"sandbox_id": "bridge:abc"}, {}, None),
        ({**_WORKER}, {"sandbox_id": "sandbox", "sandbox_owner": "mda"}, None),
        (
            {**_WORKER, "sandbox_id": "sandbox"},
            {"sandbox_id": "sandbox", "sandbox_owner": "mda"},
            _HOST,
        ),
        ({**_WORKER, "sandbox_id": "sandbox"}, {"sandbox_id": "sandbox"}, None),
    ],
)
async def test_mda_owns_new_threads_and_its_own_sandboxes(
    monkeypatch, metadata: dict[str, object], host: dict[str, object], owner: str | None
) -> None:
    """MDA creates new threads' sandboxes; a sandbox the backend made before is kept; workers share MDA's.

    A run reports its sandbox behind its preparation only where preparing reaches none, and
    the deployment has not turned that off."""
    from openswe.sandboxes.managed import ManagedSandbox

    workspace = Workspace(
        slug="default",
        snapshot_status="ready",
        snapshot_id="snap-1",
        create_params={
            "proxy_config": {
                "rules": [
                    {
                        "name": "aws",
                        "type": "aws",
                        "aws": {
                            "role_arn": "arn:aws:iam::123456789012:role/ReadOnly",
                            "subject_scope": "tag:00000000-0000-0000-0000-000000000001",
                        },
                    }
                ]
            },
            "preserve_memory_on_stop": False,
            "tag_value_ids": ["00000000-0000-0000-0000-000000000001"],
        },
    )

    async def load(slug: str | None) -> Workspace:
        return workspace

    if isinstance(metadata.get("sandbox_id"), str):
        metadata["sandbox_base_proxy_config"] = workspace.create_params["proxy_config"]
    if host.get("sandbox_owner") == "mda":
        host["sandbox_base_proxy_config"] = workspace.create_params["proxy_config"]

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return {**host} if thread_id == _HOST else metadata

    async def exists(sandbox_id: str) -> bool:
        return sandbox_id != "gone-sandbox"

    unbound: list[dict[str, object]] = []

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        unbound.append(metadata)

    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.sandbox_exists", exists)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", write_metadata)

    sandbox = await ManagedSandbox.for_thread("thread-1", None, prepare_reaches_sandbox=False)

    handed_over = metadata.get("sandbox_id") == "gone-sandbox"
    assert unbound == (
        [{"sandbox_id": None, "sandbox_base_proxy_config": None}] if handed_over else []
    )

    if owner is None:
        assert sandbox is None
        return
    assert sandbox is not None
    assert sandbox.thread_id == (None if owner == "own" else owner)
    assert sandbox.sandbox_id == (
        metadata.get("sandbox_id")
        if owner == _HOST or metadata.get("sandbox_owner") == "mda"
        else None
    )
    assert sandbox.prepare_uses_github is ("sandbox_handoff_from" in metadata)
    assert sandbox.bind_after_prepare is (
        owner == "own" and not {"sandbox_handoff_from", "slack_files_pending"} & set(metadata)
    )
    # A review prepares in its sandbox, and the deployment can turn the late report off.
    review = await ManagedSandbox.for_thread("thread-1", None)
    monkeypatch.setenv("MDA_BIND_SANDBOX_AFTER_PREPARE", "false")
    disabled = await ManagedSandbox.for_thread("thread-1", None, prepare_reaches_sandbox=False)
    assert review is not None and not review.bind_after_prepare
    assert disabled is not None and not disabled.bind_after_prepare
    assert sandbox.create.snapshot_id == "snap-1"
    assert sandbox.create.extra_fields == {
        **workspace.create_params,
        "preserve_memory_on_stop": True,
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

    workspace = Workspace(
        slug="default", create_params={"proxy_config": {"rules": []}}, snapshot_status="ready"
    )
    updates: list[dict[str, object]] = []

    async def load(slug: str | None) -> Workspace:
        return workspace

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return {**metadata, **(updates[-1] if updates else {})}

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        updates.append(metadata)

    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.threads.store.THREADS.update", write_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.maybe_start_update", _no_update)

    if "sandbox_replacement" in metadata and "sandbox_id" not in metadata:
        with pytest.raises(ValueError, match="not privately pinned"):
            await bind_mda_sandbox("thread-1", reported)
        await request_mda_replacement(
            "thread-1", workspace_slug="default", source="base", invocation_id="request-1"
        )
        updates.clear()

    profile = await ExecutionProfile.resolve(None, thread_id="thread-1")
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
async def test_a_sandbox_mda_replaced_from_another_workspace_gets_that_workspaces_proxy(
    monkeypatch,
    registry_db,
) -> None:
    from openswe.execution import SANDBOX_EXECUTION
    from openswe.sandboxes.lifecycle import reconnect_replaced_sandbox, request_mda_replacement

    other = Workspace(slug="other", create_params={"proxy_config": {"rules": ["other"]}})
    thread_metadata: dict[str, object] = {
        "sandbox_id": "mda-sandbox",
        "sandbox_owner": "mda",
        "sandbox_base_proxy_config": {"rules": ["original"]},
    }
    updates: list[dict[str, object]] = []
    publication_failures: list[Exception] = [RuntimeError("metadata unavailable")]

    async def load(slug: str | None) -> Workspace | None:
        return other if slug == "other" else None

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return thread_metadata

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        if publication_failures:
            raise publication_failures.pop()
        updates.append(metadata)
        thread_metadata.update(metadata)

    ensure = AsyncMock()
    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.threads.store.THREADS.update", write_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.ensure_sandbox_for_thread", ensure)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.maybe_start_update", AsyncMock())

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await request_mda_replacement(
            "thread-1", workspace_slug="other", source="workspace", invocation_id="request-1"
        )
    requested = await request_mda_replacement(
        "thread-1", workspace_slug="other", source="workspace", invocation_id="request-1"
    )
    assert (
        await request_mda_replacement(
            "thread-1", workspace_slug="other", source="workspace", invocation_id="request-1"
        )
        == requested
    )
    queued = await ExecutionProfile.resolve(None, thread_id="thread-1")
    other.create_params["proxy_config"] = {"rules": ["changed"]}
    with pytest.raises(ValueError, match="different replacement requires a new run"):
        await request_mda_replacement(
            "thread-1", workspace_slug="other", source="workspace", invocation_id="request-1"
        )
    with pytest.raises(ValueError, match="has not completed"):
        await request_mda_replacement(
            "thread-1", workspace_slug="other", source="workspace", invocation_id="request-2"
        )
    existing = await SandboxCreateConfig.for_thread(thread_metadata, "other", thread_id="thread-1")
    assert existing.proxy_config == {"rules": ["original"]}
    with pytest.raises(ValueError, match="does not own"):
        await reconnect_replaced_sandbox("thread-1", invocation_id="request-2")
    publication_failures.append(RuntimeError("metadata unavailable"))
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        await reconnect_replaced_sandbox("thread-1", invocation_id="request-1")
    with pytest.raises(ValueError, match="has not completed"):
        await request_mda_replacement(
            "thread-1", workspace_slug="other", source="workspace", invocation_id="request-2"
        )
    await reconnect_replaced_sandbox(
        "thread-1", workspace_slug="original", invocation_id="request-1"
    )
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
    existing = await SandboxCreateConfig.for_thread(thread_metadata, "other", thread_id="thread-1")
    assert existing.proxy_config == {"rules": ["other"]}
    other.create_params["proxy_config"] = {"rules": ["other"]}
    await request_mda_replacement(
        "thread-1", workspace_slug="other", source="workspace", invocation_id="request-2"
    )
    with pytest.raises(ValueError, match="does not own"):
        await reconnect_replaced_sandbox("thread-1", invocation_id="request-1")
    with ExecutionProfile.bind(queued), pytest.raises(ValueError, match="changed; start a new run"):
        await SandboxCreateConfig.for_thread({}, "other", thread_id="thread-1")
    ensure.assert_awaited_once_with(
        "thread-1",
        workspace_slug="original",
        github_proxy_repositories=None,
        github_proxy_permissions=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("status", ["error", "interrupted"])
async def test_failed_replacement_can_be_retried_without_accepting_stale_callbacks(
    monkeypatch, registry_db, execution_defaults: ExecutionDefaults, enabled: bool, status: str
) -> None:
    import asyncio
    from datetime import UTC, datetime

    from openswe.execution import SANDBOX_EXECUTION
    from openswe.run_config import RunConfig
    from openswe.sandboxes.lifecycle import reconnect_replaced_sandbox, request_mda_replacement
    from openswe.threads.runtimes import RUNTIMES, MdaRun, MdaRuntime

    execution_defaults.enabled = enabled
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
    await request_mda_replacement("thread-1", workspace_slug=None, source="base")
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
                workspace_slug=None,
                source="base",
                invocation_id="invocation-2",
                run_id="runtime-2",
            )
    runtime_run.side_effect = RuntimeError("runtime unavailable")
    with pytest.raises(RuntimeError, match="runtime unavailable"):
        await request_mda_replacement(
            "thread-1",
            workspace_slug=None,
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
        workspace_slug=None,
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


async def _no_update(workspace: object) -> None:
    return None


class TestLangSmithSandboxCredentials:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("linked_github", [False, True])
    async def test_cloud_lifecycle_never_borrows_github_credentials(
        self, monkeypatch, registry_db, linked_github: bool
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
        workspace = Workspace(
            slug="default",
            snapshot_status="ready",
            snapshot_id="snap-1",
            create_params={"proxy_config": base_config, "tag_value_ids": [tag_id]},
        )
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
        monkeypatch.setenv("MDA_GITHUB_PROXY_CONNECTION", "github-installation")
        monkeypatch.setattr(User, "get", AsyncMock(return_value=owner))
        monkeypatch.setattr(lifecycle, "load_workspace", AsyncMock(return_value=workspace))
        monkeypatch.setattr(lifecycle, "get_sandbox_metadata", AsyncMock(return_value=metadata))
        monkeypatch.setattr(
            "openswe.sandboxes.managed.get_sandbox_metadata", AsyncMock(return_value=metadata)
        )
        monkeypatch.setattr(lifecycle, "workspace_token", mint)
        monkeypatch.setattr(lifecycle, "thread_token_repositories", scope)
        monkeypatch.setattr(lifecycle, "configure_sandbox_proxy", configure)
        monkeypatch.setattr(lifecycle, "configure_git_identity", AsyncMock())
        monkeypatch.setattr(lifecycle, "connect_existing_sandbox", AsyncMock(return_value=backend))
        monkeypatch.setattr(lifecycle, "complete_handoff", AsyncMock())
        monkeypatch.setattr(lifecycle, "set_sandbox_backend", lambda thread_id, backend: backend)
        monkeypatch.setattr(lifecycle, "maybe_start_update", AsyncMock())
        monkeypatch.setattr("openswe.threads.store.THREADS.update", AsyncMock())
        monkeypatch.setattr(
            "openswe.sandboxes.tool_access.tool_proxy_rule", AsyncMock(return_value=tools_rule)
        )

        managed = await ManagedSandbox.for_thread("ls-thread", "default")
        assert managed is not None
        assert managed.github_proxy is None
        assert managed.create.snapshot_id == "snap-1"
        await lifecycle.bind_mda_sandbox("ls-thread", "sandbox-ls", workspace_slug="default")
        metadata.update(
            sandbox_id="sandbox-ls", sandbox_owner="mda", sandbox_base_proxy_config=base_config
        )
        await lifecycle.reconnect_replaced_sandbox("ls-thread", workspace_slug="default")
        assert await lifecycle.refresh_thread_github_proxy("ls-thread") is None
        assert configure.await_count == 2
        for call in configure.await_args_list:
            assert call.args == ("sandbox-ls", None)
            clean_config = await sandbox_proxy_config(*call.args, **call.kwargs)
            assert clean_config["rules"] == [tools_rule, cloud_rule]
        mint.assert_not_awaited()
        scope.assert_not_awaited()
