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
        ({"sandbox_id": "mda-box", "sandbox_owner": "mda"}, {}, "own"),
        ({"sandbox_id": "backend-box"}, {}, None),
        # The box the backend made is gone: this run starts in MDA's instead of failing.
        ({"sandbox_id": "gone-box"}, {}, "own"),
        ({"sandbox_id": "bridge:abc"}, {}, None),
        ({**_WORKER}, {"sandbox_id": "box", "sandbox_owner": "mda"}, None),
        ({**_WORKER, "sandbox_id": "box"}, {"sandbox_id": "box", "sandbox_owner": "mda"}, _HOST),
        ({**_WORKER, "sandbox_id": "box"}, {"sandbox_id": "box"}, None),
    ],
)
async def test_mda_owns_new_threads_and_its_own_boxes(
    monkeypatch, metadata: dict[str, str], host: dict[str, str], owner: str | None
) -> None:
    """MDA creates new threads' boxes; a box the backend made before is kept; workers share MDA's."""
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
        return sandbox_id != "gone-box"

    unbound: list[dict[str, object]] = []

    async def write_metadata(*, thread_id: str, metadata: dict[str, object]) -> None:
        unbound.append(metadata)

    monkeypatch.setattr("openswe.sandboxes.lifecycle.load_workspace", load)
    monkeypatch.setattr("openswe.sandboxes.managed.get_sandbox_metadata", read_metadata)
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.sandbox_exists", exists)
    monkeypatch.setattr("openswe.sandboxes.lifecycle.client.threads.update", write_metadata)

    sandbox = await ManagedSandbox.for_thread("thread-1", None)

    handed_over = metadata.get("sandbox_id") == "gone-box"
    assert unbound == (
        [{"sandbox_id": None, "sandbox_base_proxy_config": None}] if handed_over else []
    )

    if owner is None:
        assert sandbox is None
        return
    assert sandbox is not None
    assert sandbox.thread_id == (None if owner == "own" else owner)
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
            "mda-box",
            {
                "sandbox_owner": "mda",
                "sandbox_id": "mda-box",
                "sandbox_base_proxy_config": {"rules": []},
            },
        ),
        # MDA could not open the box: the thread stays MDA's, and binding reports why.
        ({}, None, {"sandbox_owner": "mda"}),
        ({"sandbox_id": "mda-box", "sandbox_owner": "mda"}, "mda-box", None),
        ({"sandbox_id": "mda-box", "sandbox_owner": "mda"}, None, None),
        # A thread handed over by ``recreate_sandbox`` gets the box it asked for, once.
        (
            {"sandbox_replacement": {"workspace": "default", "source": "base"}},
            "mda-box",
            {"sandbox_owner": "mda", "sandbox_id": "mda-box", "sandbox_replacement": None},
        ),
        (
            {"sandbox_id": "old-box", "sandbox_owner": "mda"},
            "mda-box",
            {
                "sandbox_owner": "mda",
                "sandbox_id": "mda-box",
                "sandbox_base_proxy_config": {"rules": []},
            },
        ),
    ],
)
async def test_the_backend_binds_the_box_mda_reports(
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
async def test_a_box_mda_replaced_from_another_workspace_gets_that_workspaces_proxy(
    monkeypatch,
) -> None:
    from unittest.mock import AsyncMock

    from openswe.sandboxes.lifecycle import reconnect_replaced_sandbox

    other = Workspace(slug="other", create_params={"proxy_config": {"rules": ["other"]}})
    metadata = {
        "sandbox_id": "mda-box",
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
