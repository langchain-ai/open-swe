"""Get-or-create, replace, reset and recreate, with the platform seams faked.

The lifecycle owns *when* a sandbox is created, reconnected, replaced or
rebound; what it boots from and which credentials it carries are injected, so
these tests supply both and assert the sequencing around them.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langsmith.sandbox import SandboxClientError

from coding_agent.sandboxes.credentials import InstalledCredentials
from coding_agent.sandboxes.git_identity import GitIdentity
from coding_agent.sandboxes.lifecycle import SandboxCreateConfig, SandboxLifecycle
from coding_agent.sandboxes.providers.registry import SandboxGoneError
from coding_agent.sandboxes.state import (
    SANDBOX_BACKENDS,
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
    set_sandbox_backend,
)

IDENTITY = GitIdentity("test-bot", "test-bot@example.com")


def _backend(sandbox_id: str) -> MagicMock:
    return MagicMock(id=sandbox_id, aexecute=AsyncMock())


@dataclass
class FakeInstalled:
    credentials: FakeCredentials
    base_config: dict[str, Any] | None

    async def bind(self, thread_id: str | None) -> None:
        self.credentials.bound.append(thread_id)


@dataclass
class FakeCredentials:
    recorded: dict[str, Any] | None = None
    errors: list[Exception] = field(default_factory=list)
    installs: list[tuple[str, dict[str, Any] | None]] = field(default_factory=list)
    bound: list[str | None] = field(default_factory=list)

    async def install(
        self,
        sandbox_id: str,
        *,
        thread_id: str | None = None,
        base_proxy_config: dict[str, Any] | None = None,
    ) -> InstalledCredentials:
        if self.errors:
            raise self.errors.pop(0)
        self.installs.append((sandbox_id, base_proxy_config))
        return FakeInstalled(self, base_proxy_config)

    async def recorded_base_config(self, thread_id: str | None) -> dict[str, Any] | None:
        return self.recorded


@dataclass
class FakeCreateConfig:
    config: SandboxCreateConfig = field(
        default_factory=lambda: SandboxCreateConfig(snapshot_id="snap")
    )
    slugs: list[str | None] = field(default_factory=list)

    async def __call__(self, environment_slug: str | None) -> SandboxCreateConfig:
        self.slugs.append(environment_slug)
        return self.config


@dataclass
class FakeProvider:
    """Stands in for the provider registry: boots new sandboxes and reconnects."""

    created: Any = None
    existing: Any = None
    create_error: Exception | None = None
    connect_error: Exception | None = None
    boots: list[dict[str, Any]] = field(default_factory=list)
    connects: list[str] = field(default_factory=list)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if args:
            self.connects.append(args[0])
            if self.connect_error is not None:
                raise self.connect_error
            return self.existing
        self.boots.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        return self.created


def _lifecycle(
    credentials: FakeCredentials,
    create_config: FakeCreateConfig | None = None,
) -> SandboxLifecycle:
    return SandboxLifecycle(
        resolve_create_config=create_config or FakeCreateConfig(),
        credentials=credentials,
        git_identity=IDENTITY,
    )


@contextmanager
def _wired(
    provider: FakeProvider,
    *,
    sandbox_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    update: AsyncMock | None = None,
) -> Iterator[AsyncMock]:
    thread_update = update if update is not None else AsyncMock()
    with (
        patch(
            "coding_agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
            new_callable=AsyncMock,
            return_value=sandbox_id,
        ),
        patch(
            "coding_agent.sandboxes.lifecycle.get_sandbox_metadata",
            new_callable=AsyncMock,
            return_value=metadata or {},
        ),
        patch("coding_agent.sandboxes.lifecycle.create_sandbox", provider),
        patch("coding_agent.sandboxes.lifecycle.client.threads.update", thread_update),
    ):
        yield thread_update


@pytest.fixture(autouse=True)
def _clear_registry() -> Iterator[None]:
    SANDBOX_BACKENDS.clear()
    yield
    SANDBOX_BACKENDS.clear()


@pytest.mark.asyncio
async def test_creates_new_sandbox_when_metadata_has_none() -> None:
    credentials = FakeCredentials()
    create_config = FakeCreateConfig()
    provider = FakeProvider(created=_backend("sandbox-new"))

    with _wired(provider) as update:
        result = await _lifecycle(credentials, create_config).ensure_for_thread(
            "thread-new", environment_slug="large"
        )

    assert result.id == "sandbox-new"
    assert create_config.slugs == ["large"]
    assert provider.boots == [{"snapshot_id": "snap"}]
    assert credentials.installs == [("sandbox-new", None)]
    assert credentials.bound == ["thread-new"]
    assert update.await_args_list[-1].kwargs == {
        "thread_id": "thread-new",
        "metadata": {"sandbox_id": "sandbox-new"},
    }


@pytest.mark.asyncio
async def test_new_sandbox_persists_the_installed_base_config() -> None:
    base_config = {"rules": [{"name": "public-api", "match_hosts": ["example.com"]}]}
    credentials = FakeCredentials()
    create_config = FakeCreateConfig(
        SandboxCreateConfig(snapshot_id="snap", create_params={"proxy_config": base_config})
    )
    provider = FakeProvider(created=_backend("sandbox-new"))

    with _wired(provider) as update:
        await _lifecycle(credentials, create_config).ensure_for_thread("thread-proxy-config")

    assert credentials.installs == [("sandbox-new", base_config)]
    update.assert_awaited_once_with(
        thread_id="thread-proxy-config",
        metadata={"sandbox_id": "sandbox-new", "sandbox_base_proxy_config": base_config},
    )


@pytest.mark.asyncio
async def test_reconnects_to_the_sandbox_in_metadata() -> None:
    credentials = FakeCredentials()
    provider = FakeProvider(existing=_backend("sandbox-existing"))
    base_config = {"rules": []}

    with _wired(
        provider,
        sandbox_id="sandbox-existing",
        metadata={
            "sandbox_id": "sandbox-existing",
            "sandbox_base_proxy_config": base_config,
        },
    ) as update:
        result = await _lifecycle(credentials).ensure_for_thread("thread-reconnect")

    assert result.id == "sandbox-existing"
    assert provider.connects == ["sandbox-existing"]
    # Reinstalled with the config the thread was last bound with, not a fresh one.
    assert credentials.installs == [("sandbox-existing", base_config)]
    assert credentials.bound == ["thread-reconnect"]
    # Metadata already holds this id, so no update is issued.
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_falls_back_to_the_recorded_base_config() -> None:
    recorded = {"rules": [{"name": "recorded"}]}
    credentials = FakeCredentials(recorded=recorded)
    provider = FakeProvider(existing=_backend("sandbox-existing"))

    with _wired(provider, sandbox_id="sandbox-existing", metadata={"sandbox_id": "x"}):
        await _lifecycle(credentials).ensure_for_thread("thread-recorded")

    assert credentials.installs == [("sandbox-existing", recorded)]


@pytest.mark.asyncio
async def test_resolves_an_unresolved_backend_proxy() -> None:
    thread_id = "thread-unresolved-proxy"
    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    existing = _backend("sandbox-existing")
    provider = FakeProvider(existing=existing)

    with _wired(provider, sandbox_id="sandbox-existing") as update:
        result = await _lifecycle(FakeCredentials()).ensure_for_thread(thread_id)

    assert result is proxy
    assert proxy.current is existing
    update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_step", ["create", "bind_thread"])
async def test_initialization_failure_publishes_nothing(failing_step: str) -> None:
    """A caller reads the cached backend without awaiting the startup task.

    Publishing before initialization finishes would hand the rest of the run a
    sandbox whose setup failed, with the failure visible only in a done callback.
    """
    thread_id = "thread-init-fails"
    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    failure = RuntimeError("initialization failed")
    provider = FakeProvider(
        created=_backend("sandbox-new"),
        create_error=failure if failing_step == "create" else None,
    )
    update = AsyncMock(side_effect=failure if failing_step == "bind_thread" else None)

    with (
        _wired(provider, update=update),
        pytest.raises(RuntimeError, match="initialization failed"),
    ):
        await _lifecycle(FakeCredentials()).ensure_for_thread(thread_id)

    assert not proxy.has_backend
    assert not SANDBOX_BACKENDS[thread_id].has_backend


@pytest.mark.asyncio
async def test_credential_install_failure_makes_a_sandbox_unreachable() -> None:
    """A sandbox we can't reconfigure fails the run, and is never swapped out.

    Replacing it would hand the agent an empty filesystem and discard any work
    the old sandbox still held.
    """
    credentials = FakeCredentials(errors=[RuntimeError("proxy config rejected")])
    provider = FakeProvider(existing=_backend("sandbox-stale"))

    with (
        _wired(provider, sandbox_id="sandbox-stale"),
        pytest.raises(SandboxUnreachableError) as excinfo,
    ):
        await _lifecycle(credentials).ensure_for_thread("thread-stale")

    assert excinfo.value.sandbox_id == "sandbox-stale"
    assert provider.boots == []


@pytest.mark.asyncio
async def test_unreachable_sandbox_fails_instead_of_being_replaced() -> None:
    credentials = FakeCredentials()
    provider = FakeProvider(
        created=_backend("sandbox-replacement"),
        connect_error=RuntimeError("Sandbox 'sandbox-deleted' not found"),
    )

    with (
        _wired(provider, sandbox_id="sandbox-deleted"),
        pytest.raises(SandboxUnreachableError),
    ):
        await _lifecycle(credentials).ensure_for_thread("thread-dead-sandbox")

    assert provider.boots == []


@pytest.mark.asyncio
async def test_deleted_sandbox_is_replaced_without_opting_in() -> None:
    """A deleted sandbox holds nothing, and the stale id would brick the thread."""
    thread_id = "thread-gone-sandbox"
    credentials = FakeCredentials()
    provider = FakeProvider(
        created=_backend("sandbox-replacement"),
        connect_error=SandboxGoneError("Sandbox 'sandbox-deleted' not found"),
    )

    async def persist_metadata(**_kwargs: object) -> None:
        # The thread binds to the sandbox only once it is created and initialized.
        assert credentials.bound == [thread_id]

    update = AsyncMock(side_effect=persist_metadata)

    with _wired(provider, sandbox_id="sandbox-deleted", update=update):
        result = await _lifecycle(credentials).ensure_for_thread(thread_id)

    assert result.id == "sandbox-replacement"
    assert update.await_args_list[-1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-replacement"},
    }


@pytest.mark.asyncio
async def test_replaces_an_unreachable_sandbox_when_replacement_is_allowed() -> None:
    """Only callers whose sandbox holds nothing re-derivable opt in."""
    thread_id = "thread-dead-sandbox-replaceable"
    create_config = FakeCreateConfig()
    provider = FakeProvider(
        created=_backend("sandbox-replacement"),
        connect_error=RuntimeError("Sandbox 'sandbox-deleted' not found"),
    )

    with _wired(provider, sandbox_id="sandbox-deleted") as update:
        result = await _lifecycle(FakeCredentials(), create_config).ensure_for_thread(
            thread_id, environment_slug="large", allow_replacement=True
        )

    assert result.id == "sandbox-replacement"
    assert create_config.slugs == ["large"]
    # The stale id is cleared by persisting the replacement, so later runs stop
    # reconnecting to a sandbox that no longer exists.
    assert update.await_args_list[-1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-replacement"},
    }


@pytest.mark.asyncio
async def test_replaces_an_unreachable_cached_sandbox_in_place() -> None:
    thread_id = "thread-dead-cache"
    proxy = set_sandbox_backend(thread_id, _backend("sandbox-cached-dead"))
    replacement = _backend("sandbox-replacement")
    credentials = FakeCredentials(errors=[SandboxClientError("sandbox is gone")])
    provider = FakeProvider(created=replacement)

    with _wired(provider, sandbox_id="sandbox-cached-dead"):
        result = await _lifecycle(credentials).ensure_for_thread(thread_id, allow_replacement=True)

    # Replaced in place, so handles already built around the proxy stay valid.
    assert result is proxy
    assert proxy.current is replacement


@pytest.mark.asyncio
async def test_failed_replacement_still_raises_sandbox_unreachable() -> None:
    provider = FakeProvider(
        create_error=RuntimeError("sandbox API outage"),
        connect_error=RuntimeError("Sandbox 'sandbox-deleted' not found"),
    )

    with (
        _wired(provider, sandbox_id="sandbox-deleted"),
        pytest.raises(SandboxUnreachableError) as excinfo,
    ):
        await _lifecycle(FakeCredentials()).ensure_for_thread(
            "thread-replacement-fails", allow_replacement=True
        )

    # Typed, so callers still recognize it and notify the user.
    assert excinfo.value.sandbox_id == "sandbox-deleted"
    assert "sandbox API outage" in str(excinfo.value)


@pytest.mark.asyncio
async def test_recreate_hands_off_after_metadata_persists() -> None:
    thread_id = "thread-recreate"
    old_sandbox = _backend("sandbox-old")
    new_sandbox = _backend("sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)
    credentials = FakeCredentials(recorded=None)
    provider = FakeProvider(created=new_sandbox)

    async def persist_metadata(**_kwargs: object) -> None:
        assert proxy.current is old_sandbox

    update = AsyncMock(side_effect=persist_metadata)

    with _wired(provider, sandbox_id="sandbox-old", update=update):
        result = await _lifecycle(credentials).recreate_for_thread(thread_id)

    assert result == ("sandbox-old", "sandbox-new")
    update.assert_awaited_once_with(thread_id=thread_id, metadata={"sandbox_id": "sandbox-new"})
    assert SANDBOX_BACKENDS[thread_id] is proxy
    assert proxy.current is new_sandbox


@pytest.mark.asyncio
async def test_recreate_persists_the_recorded_base_config() -> None:
    thread_id = "thread-recreate-proxy"
    recorded = {"rules": []}
    set_sandbox_backend(thread_id, _backend("sandbox-old"))
    provider = FakeProvider(created=_backend("sandbox-new"))

    with _wired(provider, sandbox_id="sandbox-old") as update:
        await _lifecycle(FakeCredentials(recorded=recorded)).recreate_for_thread(thread_id)

    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={"sandbox_id": "sandbox-new", "sandbox_base_proxy_config": recorded},
    )


@pytest.mark.asyncio
async def test_recreate_keeps_the_old_binding_when_metadata_update_fails() -> None:
    thread_id = "thread-recreate-failure"
    old_sandbox = _backend("sandbox-old")
    proxy = set_sandbox_backend(thread_id, old_sandbox)
    provider = FakeProvider(created=_backend("sandbox-new"))
    update = AsyncMock(side_effect=RuntimeError("metadata unavailable"))

    with (
        _wired(provider, sandbox_id="sandbox-old", update=update),
        pytest.raises(RuntimeError, match="metadata unavailable"),
    ):
        await _lifecycle(FakeCredentials()).recreate_for_thread(thread_id)

    assert SANDBOX_BACKENDS[thread_id] is proxy
    assert proxy.current is old_sandbox


@pytest.mark.asyncio
async def test_recreate_rejects_a_non_distinct_provider_result() -> None:
    thread_id = "thread-recreate-same-id"
    old_sandbox = _backend("sandbox-same")
    set_sandbox_backend(thread_id, old_sandbox)
    same = _backend("sandbox-same")
    provider = FakeProvider(created=same)

    with (
        _wired(provider, sandbox_id="sandbox-same", update=AsyncMock()) as update,
        pytest.raises(RuntimeError, match="distinct sandbox"),
    ):
        await _lifecycle(FakeCredentials()).recreate_for_thread(thread_id)

    update.assert_not_awaited()
    assert SANDBOX_BACKENDS[thread_id].current is old_sandbox


@pytest.mark.asyncio
async def test_reset_hands_off_after_metadata_persists() -> None:
    thread_id = "thread-reset"
    create_params: dict[str, Any] = {
        "snapshot_name": "python:latest",
        "cpu_millicores": 500,
        "_internal_runtime": "v2",
        "proxy_config": {"rules": []},
    }
    old_sandbox = _backend("sandbox-old")
    new_sandbox = _backend("sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)
    credentials = FakeCredentials()

    async def persist_metadata(**_kwargs: object) -> None:
        assert proxy.current is old_sandbox
        # The token is recorded for the thread only once the thread is bound to
        # the sandbox it was minted for.
        assert credentials.bound == []

    update = AsyncMock(side_effect=persist_metadata)

    with (
        _wired(FakeProvider(), sandbox_id="sandbox-old", update=update),
        patch(
            "coding_agent.sandboxes.lifecycle.create_langsmith_sandbox_from_params",
            new_callable=AsyncMock,
            return_value=new_sandbox,
        ) as create,
    ):
        result = await _lifecycle(credentials).reset_for_thread(thread_id, create_params)

    assert result == ("sandbox-old", "sandbox-new")
    create.assert_awaited_once_with(create_params)
    assert credentials.installs == [("sandbox-new", {"rules": []})]
    assert credentials.bound == [thread_id]
    new_sandbox.aexecute.assert_awaited_once()
    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={"sandbox_id": "sandbox-new", "sandbox_base_proxy_config": {"rules": []}},
    )
    assert proxy.current is new_sandbox


@pytest.mark.asyncio
async def test_reset_clears_stale_proxy_metadata() -> None:
    thread_id = "thread-reset-default-proxy"
    set_sandbox_backend(thread_id, _backend("sandbox-old"))

    with (
        _wired(FakeProvider(), sandbox_id="sandbox-old") as update,
        patch(
            "coding_agent.sandboxes.lifecycle.create_langsmith_sandbox_from_params",
            new_callable=AsyncMock,
            return_value=_backend("sandbox-new"),
        ),
    ):
        await _lifecycle(FakeCredentials()).reset_for_thread(thread_id, {})

    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={"sandbox_id": "sandbox-new", "sandbox_base_proxy_config": None},
    )


@pytest.mark.asyncio
async def test_reset_does_not_record_credentials_before_metadata_persists() -> None:
    thread_id = "thread-reset-failure"
    old_sandbox = _backend("sandbox-old")
    proxy = set_sandbox_backend(thread_id, old_sandbox)
    credentials = FakeCredentials()
    update = AsyncMock(side_effect=RuntimeError("metadata unavailable"))

    with (
        _wired(FakeProvider(), sandbox_id="sandbox-old", update=update),
        patch(
            "coding_agent.sandboxes.lifecycle.create_langsmith_sandbox_from_params",
            new_callable=AsyncMock,
            return_value=_backend("sandbox-new"),
        ),
        pytest.raises(RuntimeError, match="metadata unavailable"),
    ):
        await _lifecycle(credentials).reset_for_thread(thread_id, {"proxy_config": {"rules": []}})

    assert credentials.bound == []
    assert proxy.current is old_sandbox


@pytest.mark.asyncio
async def test_reset_rejects_other_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "modal")

    with pytest.raises(ValueError, match="only supported by the LangSmith"):
        await _lifecycle(FakeCredentials()).reset_for_thread("thread-reset", {})
