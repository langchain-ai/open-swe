"""Unit tests for agent/user memory remount and permissions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from managed_deepagents._channels.messaging_registry import register_channel_messaging
from managed_deepagents._memory.mounts import UnmountedMemoryBackend
from managed_deepagents._memory.user_memory import UserMemoryBackend, can_mount_user_memory
from managed_deepagents.runtime import (
    _managed_context_backend,
    _managed_context_permissions,
)


class _ProbeBackend:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def download_files(self, paths: list[str]) -> list[Any]:
        out: list[Any] = []
        for path in paths:
            content = self.files.get(path)
            if content is None:
                out.append(SimpleNamespace(path=path, error="file_not_found", content=None))
            else:
                out.append(SimpleNamespace(path=path, error=None, content=content.encode()))
        return out

    def read(self, path: str, offset: int = 0, limit: int = 2000) -> Any:
        return SimpleNamespace(path=path, content=self.files.get(path, ""))

    def write(self, path: str, content: str) -> Any:
        self.files[path] = content
        return SimpleNamespace(path=path)


class _FakeCompositeBackend:
    def __init__(self, *, default: Any, routes: dict[str, Any]) -> None:
        self.default = default
        self.routes = routes


class _FakeStateBackend:
    def __init__(self) -> None:
        self.runtime = None


def _auth_config(user_id: str) -> dict[str, Any]:
    """A run config carrying the trusted caller the auth handler resolved."""
    return {
        "configurable": {
            "langgraph_auth_user": {
                "identity": "deployment-owner",
                "permissions": [],
                "mda_user_id": user_id,
            }
        }
    }


def test_managed_context_backend_mounts_agent_layer() -> None:
    backend = _ProbeBackend({})
    composite = _managed_context_backend(
        backend,
        None,
        _FakeCompositeBackend,
        _FakeStateBackend,
        mount_agent_memory=True,
    )

    assert isinstance(composite, _FakeCompositeBackend)
    assert set(composite.routes) == {
        "/instructions.md/",
        "/skills/",
        "/memories/agent/",
        "/memories/user/",
    }
    assert composite.routes["/memories/agent/"]._repo_prefix == "memories/agent"
    assert isinstance(composite.routes["/memories/user/"], UnmountedMemoryBackend)


@pytest.mark.parametrize("layer", ["agent", "user"])
def test_unmounted_memory_layer_rejects_writes_instead_of_dropping_them(
    layer: str, tmp_path: Path
) -> None:
    """A write to an unmounted layer must not succeed in the default backend (MDAC-89)."""
    roots = {name: tmp_path / name for name in ("hub", "user", "sandbox")}
    for root in roots.values():
        root.mkdir()

    def disk(name: str) -> FilesystemBackend:
        return FilesystemBackend(root_dir=roots[name], virtual_mode=True)

    composite = _managed_context_backend(
        disk("hub"),
        disk("sandbox"),
        CompositeBackend,
        StateBackend,
        mount_agent_memory=layer == "user",
        user_memory_backend=disk("user") if layer == "agent" else None,
    )
    path = f"/memories/{layer}/AGENTS.md"

    for result in [composite.write(path, "x"), asyncio.run(composite.awrite(path, "x"))]:
        assert result.error == (
            f"Cannot write {path}: {layer} memory is not mounted for this run. Nothing was saved."
        )
        assert result.path is None
    edited = composite.edit(path, "a", "b")
    assert edited.error is not None
    assert edited.error.startswith(f"Cannot edit {path}:")
    assert composite.read(path).error == (
        f"Cannot read {path}: {layer} memory is not mounted for this run."
    )
    assert composite.ls(f"/memories/{layer}/").error is not None
    assert [r.error for r in composite.upload_files([(path, b"x")])] == ["permission_denied"]
    assert [r.error for r in composite.download_files([path])] == ["permission_denied"]
    # Root searches fan out to every route; an unmounted layer contributes nothing.
    assert composite.grep("x", "/").error is None
    assert composite.glob("**/*.md", "/").error is None
    assert [list(root.iterdir()) for root in roots.values()] == [[], [], []]


def test_unmounted_memory_layer_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="traversal"):
        UnmountedMemoryBackend("user").write("/../agent/AGENTS.md", "x")


def test_managed_context_backend_mounts_user_repo_root() -> None:
    backend = _ProbeBackend({})
    user_backend = _ProbeBackend({})
    composite = _managed_context_backend(
        backend,
        None,
        _FakeCompositeBackend,
        _FakeStateBackend,
        user_memory_backend=user_backend,
    )

    assert composite.routes["/memories/user/"]._repo_prefix == ""


def test_user_memory_uses_opaque_isolated_repo_handles(monkeypatch: Any) -> None:
    opened: list[tuple[str, _ProbeBackend]] = []

    def context_backend(repo: str) -> _ProbeBackend:
        backend = _ProbeBackend({})
        opened.append((repo, backend))
        return backend

    monkeypatch.setattr(
        "managed_deepagents._memory.user_memory.ContextHubBackend",
        context_backend,
    )

    def memory(user_id: str) -> UserMemoryBackend:
        return UserMemoryBackend(
            deploy_handle="my-agent",
            filesystem_root=None,
            run_config=_auth_config(user_id),
            allowed_user_id=user_id,
        )

    alice = memory("owner-alice")
    alice.read("/AGENTS.md")
    alice.read("/AGENTS.md")
    bob = memory("owner-bob")
    bob.read("/AGENTS.md")

    alice_handle, alice_repo = opened[0]
    bob_handle, _ = opened[1]
    assert alice_handle.startswith("my-agent--u--")
    assert "owner-alice" not in alice_handle
    assert alice_handle != bob_handle
    assert "/AGENTS.md" not in alice_repo.files
    assert len(opened) == 2


def test_user_memory_accepts_an_id_that_matches_the_principal(monkeypatch: Any) -> None:
    opened: list[str] = []

    def context_backend(repo: str) -> _ProbeBackend:
        opened.append(repo)
        return _ProbeBackend({})

    monkeypatch.setattr(
        "managed_deepagents._memory.user_memory.ContextHubBackend",
        context_backend,
    )
    backend = UserMemoryBackend(
        deploy_handle="my-agent",
        filesystem_root=None,
        run_config=_auth_config("owner-alice"),
        asserted_user_id="owner-alice",
        allowed_user_id="owner-alice",
    )

    backend.read("/AGENTS.md")

    assert len(opened) == 1
    assert opened[0].startswith("my-agent--u--")
    assert "owner-alice" not in opened[0]


def test_user_memory_refuses_an_id_that_names_another_principal() -> None:
    # The declared id is an assertion, never a selector: a mismatch fails the run
    # rather than reaching the named caller's memory.
    backend = UserMemoryBackend(
        deploy_handle="my-agent",
        filesystem_root=None,
        run_config=_auth_config("owner-alice"),
        asserted_user_id="owner-bob",
    )

    with pytest.raises(ValueError, match="must match the authenticated principal"):
        backend.read("/AGENTS.md")


def test_user_memory_fails_closed_without_trusted_identity() -> None:
    backend = UserMemoryBackend(
        deploy_handle="my-agent",
        filesystem_root=None,
        run_config={"configurable": {}},
    )

    with pytest.raises(ValueError, match="User memory requires a trusted caller identity"):
        backend.read("/AGENTS.md")


def test_managed_context_permissions_deny_skills_writes() -> None:
    perms = _managed_context_permissions(
        None,
        lambda **kwargs: kwargs,
    )
    assert perms == [
        {
            "operations": ["write"],
            "paths": ["/skills/**"],
            "mode": "deny",
        }
    ]


def test_user_memory_requires_a_resolved_access_decision() -> None:
    config = _auth_config("alice")
    config["configurable"]["langgraph_auth_user"].update(
        mda_source_provider="slack",
        mda_source_thread_id="shared-thread",
    )
    backend = UserMemoryBackend(deploy_handle="agent", filesystem_root=None, run_config=config)
    with pytest.raises(ValueError, match="allowed caller"):
        backend.read("/AGENTS.md")


@pytest.mark.parametrize(("kind", "expected"), [("person", True), ("service", False)])
def test_slack_dm_requires_a_person(kind: str, expected: bool) -> None:
    config = _auth_config("alice")
    config["configurable"]["langgraph_auth_user"].update(
        mda_user_kind=kind,
        mda_source_provider="slack",
        mda_source_thread_id="dm-thread",
    )
    register_channel_messaging([SimpleNamespace(name="support", kind="slack")])
    context = {
        "mda_channel": {
            "name": "support",
            "provider": "slack",
            "sourceThreadKey": "dm-thread",
            "deliveryId": "delivery",
            "transport": "direct",
            "target": {},
            "event": {"type": "message", "messages": []},
            "rawEvent": {"channel_type": "im"},
        }
    }
    assert can_mount_user_memory(config, context) is expected
