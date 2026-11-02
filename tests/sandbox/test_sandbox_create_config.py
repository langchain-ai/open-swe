"""Which workspace image a new sandbox boots from."""

import pytest

from openswe.sandboxes.lifecycle import SandboxCreateConfig
from openswe.workspaces.store import Workspace


@pytest.mark.asyncio
async def test_inherited_sandbox_tracks_default_without_changing_workspace_identity(
    monkeypatch,
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


_HOST = "coordinator-1"
_WORKER = {"task_id": "00000000-0000-0000-0000-000000000001", "sandbox_host_thread_id": _HOST}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "host", "owner"),
    [
        ({}, {}, "own"),
        ({"sandbox_id": "mda-sandbox", "sandbox_owner": "mda"}, {}, "own"),
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
    monkeypatch, metadata: dict[str, str], host: dict[str, str], owner: str | None
) -> None:
    """MDA creates new threads' sandboxes; a sandbox the backend made before is kept; workers share MDA's."""
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

    async def read_metadata(thread_id: str) -> dict[str, str]:
        return host if thread_id == _HOST else metadata

    async def exists(sandbox_id: str) -> bool:
        return sandbox_id != "gone-sandbox"

    unbound: list[dict[str, object]] = []

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        unbound.append(metadata)

    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.sandbox_exists", exists)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", write_metadata)

    sandbox = await ManagedSandbox.for_thread("thread-1", None)

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
            {"sandbox_owner": "mda", "sandbox_id": "mda-sandbox", "sandbox_replacement": None},
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
    metadata: dict[str, str],
    reported: str | None,
    update: dict[str, object] | None,
) -> None:
    from openswe.sandboxes.lifecycle import bind_mda_sandbox, ensure_sandbox_for_thread
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
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", write_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.maybe_start_update", _no_update)

    if reported is None and "sandbox_id" not in metadata:
        with pytest.raises(SandboxUnreachableError, match="could not open.*HTTP 503") as raised:
            await bind_mda_sandbox("thread-1", reported, open_error="SandboxClientError: HTTP 503")
        assert raised.value.reason == "SandboxClientError: HTTP 503"
        with pytest.raises(SandboxUnreachableError, match="could not open"):
            await ensure_sandbox_for_thread("thread-1")
    else:
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
) -> None:
    from unittest.mock import AsyncMock

    from openswe.sandboxes.lifecycle import reconnect_replaced_sandbox

    other = Workspace(slug="other", create_params={"proxy_config": {"rules": ["other"]}})
    metadata = {
        "sandbox_id": "mda-sandbox",
        "sandbox_owner": "mda",
        "sandbox_base_proxy_config": {"rules": ["original"]},
        "sandbox_replacement": {"workspace": "other", "source": "workspace"},
    }
    updates: list[dict[str, object]] = []

    async def load(slug: str | None) -> Workspace | None:
        return other if slug == "other" else None

    async def read_metadata(thread_id: str) -> dict[str, object]:
        return metadata

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        updates.append(metadata)

    ensure = AsyncMock()
    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", write_metadata)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.ensure_sandbox_for_thread", ensure)

    await reconnect_replaced_sandbox("thread-1", workspace_slug="original")

    assert updates == [
        {"sandbox_base_proxy_config": {"rules": ["other"]}, "sandbox_replacement": None}
    ]
    ensure.assert_awaited_once_with(
        "thread-1",
        workspace_slug="original",
        github_proxy_repositories=None,
        github_proxy_permissions=None,
    )


async def _no_update(workspace: object) -> None:
    return None


class TestLangSmithSandboxCredentials:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("linked_github", [False, True])
    async def test_cloud_lifecycle_never_borrows_github_credentials(
        self, monkeypatch, linked_github: bool
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
        cloud_rule = {
            "name": "aws",
            "type": "aws",
            "aws": {"role_arn": "arn:aws:iam::123456789012:role/ReadOnly"},
        }
        base_config = {"rules": [{"name": "github", "headers": [{"value": "stale"}]}, cloud_rule]}
        workspace = Workspace(slug="default", snapshot_status="ready", snapshot_id="snap-1")
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
        monkeypatch.setattr(lifecycle.client.threads, "update", AsyncMock())
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
