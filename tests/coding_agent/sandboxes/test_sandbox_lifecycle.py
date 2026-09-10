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

_MODULE = "coding_agent.sandboxes.lifecycle"
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


class FakeCreateConfig:
    """The injected resolver: records the slug it was asked about."""

    def __init__(self, config: SandboxCreateConfig | None = None) -> None:
        self.config = config or SandboxCreateConfig(snapshot_id="snap")
        self.slugs: list[str | None] = []

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
    reset_to: MagicMock | None = None,
) -> Iterator[AsyncMock]:
    """Patch the thread-metadata, provider and reset seams around one call."""
    thread_update = update if update is not None else AsyncMock()
    with (
        patch(f"{_MODULE}.get_sandbox_id_from_metadata", AsyncMock(return_value=sandbox_id)),
        patch(f"{_MODULE}.get_sandbox_metadata", AsyncMock(return_value=metadata or {})),
        patch(f"{_MODULE}.create_sandbox", provider),
        patch(f"{_MODULE}.client.threads.update", thread_update),
        patch(f"{_MODULE}.create_langsmith_sandbox_from_params", AsyncMock(return_value=reset_to)),
    ):
        yield thread_update


@pytest.fixture(autouse=True)
def _clear_registry() -> Iterator[None]:
    SANDBOX_BACKENDS.clear()
    yield
    SANDBOX_BACKENDS.clear()


@pytest.mark.parametrize("proxy_config", [None, {"rules": [{"name": "public-api"}]}])
async def test_creates_a_new_sandbox_when_the_thread_has_none(
    proxy_config: dict[str, Any] | None,
) -> None:
    credentials = FakeCredentials()
    create_config = FakeCreateConfig(
        SandboxCreateConfig(
            snapshot_id="snap",
            create_params={"proxy_config": proxy_config} if proxy_config else {},
        )
    )
    provider = FakeProvider(created=_backend("sandbox-new"))

    with _wired(provider) as update:
        result = await _lifecycle(credentials, create_config).ensure_for_thread(
            "thread-new", environment_slug="large"
        )

    assert result.id == "sandbox-new"
    assert create_config.slugs == ["large"]
    assert credentials.installs == [("sandbox-new", proxy_config)]
    assert credentials.bound == ["thread-new"]
    expected: dict[str, Any] = {"sandbox_id": "sandbox-new"}
    if proxy_config is not None:
        expected["sandbox_base_proxy_config"] = proxy_config
    update.assert_awaited_once_with(thread_id="thread-new", metadata=expected)


@pytest.mark.parametrize("source", ["metadata", "credentials"])
async def test_reconnects_with_the_config_the_thread_was_bound_with(source: str) -> None:
    thread_id = "thread-reconnect"
    proxy = get_or_create_sandbox_backend_proxy(thread_id)
    base_config = {"rules": [{"name": "recorded"}]}
    credentials = FakeCredentials(recorded=base_config if source == "credentials" else None)
    existing = _backend("sandbox-existing")
    metadata: dict[str, Any] = {"sandbox_id": "sandbox-existing"}
    if source == "metadata":
        metadata["sandbox_base_proxy_config"] = base_config
    provider = FakeProvider(existing=existing)

    with _wired(provider, sandbox_id="sandbox-existing", metadata=metadata) as update:
        result = await _lifecycle(credentials).ensure_for_thread(thread_id)

    assert result is proxy
    assert proxy.current is existing
    assert provider.connects == ["sandbox-existing"]
    assert provider.boots == []
    assert credentials.installs == [("sandbox-existing", base_config)]
    assert credentials.bound == [thread_id]
    update.assert_not_awaited()


@pytest.mark.parametrize("failing_step", ["create", "bind_thread"])
async def test_initialization_failure_publishes_nothing(failing_step: str) -> None:
    """Callers read the cached backend without awaiting the task that built it."""
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


@pytest.mark.parametrize("failure", ["reconnect", "credentials"])
async def test_an_unreachable_sandbox_fails_instead_of_being_replaced(failure: str) -> None:
    """A replacement is empty, and swapping one in discards uncommitted work."""
    credentials = FakeCredentials(
        errors=[RuntimeError("proxy config rejected")] if failure == "credentials" else []
    )
    provider = FakeProvider(
        existing=_backend("sandbox-stale"),
        created=_backend("sandbox-replacement"),
        connect_error=RuntimeError("connect timed out") if failure == "reconnect" else None,
    )

    with (
        _wired(provider, sandbox_id="sandbox-stale"),
        pytest.raises(SandboxUnreachableError) as excinfo,
    ):
        await _lifecycle(credentials).ensure_for_thread("thread-stale")

    assert excinfo.value.sandbox_id == "sandbox-stale"
    assert provider.boots == []


@pytest.mark.parametrize(
    ("connect_error", "allow_replacement"),
    [
        (SandboxGoneError("Sandbox 'sandbox-deleted' not found"), False),
        (RuntimeError("Sandbox 'sandbox-deleted' not found"), True),
    ],
    ids=["deleted", "unreachable-but-replaceable"],
)
async def test_a_lost_sandbox_is_replaced_and_rebound(
    connect_error: Exception, allow_replacement: bool
) -> None:
    """A deleted sandbox holds nothing, and its stale id would brick the thread."""
    thread_id = "thread-lost-sandbox"
    credentials = FakeCredentials()
    provider = FakeProvider(created=_backend("sandbox-replacement"), connect_error=connect_error)

    async def persist_metadata(**_kwargs: object) -> None:
        assert credentials.bound == [thread_id]

    update = AsyncMock(side_effect=persist_metadata)

    with _wired(provider, sandbox_id="sandbox-deleted", update=update):
        result = await _lifecycle(credentials).ensure_for_thread(
            thread_id, allow_replacement=allow_replacement
        )

    assert result.id == "sandbox-replacement"
    assert update.await_args_list[-1].kwargs == {
        "thread_id": thread_id,
        "metadata": {"sandbox_id": "sandbox-replacement"},
    }


async def test_replaces_an_unreachable_cached_sandbox_in_place() -> None:
    """Replaced in place, so handles already built around the proxy stay valid."""
    thread_id = "thread-dead-cache"
    proxy = set_sandbox_backend(thread_id, _backend("sandbox-cached-dead"))
    replacement = _backend("sandbox-replacement")
    credentials = FakeCredentials(errors=[SandboxClientError("sandbox is gone")])

    with _wired(FakeProvider(created=replacement), sandbox_id="sandbox-cached-dead"):
        result = await _lifecycle(credentials).ensure_for_thread(thread_id, allow_replacement=True)

    assert result is proxy
    assert proxy.current is replacement


async def test_failed_replacement_still_raises_sandbox_unreachable() -> None:
    """Typed, so callers still recognize it and notify the user."""
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

    assert excinfo.value.sandbox_id == "sandbox-deleted"
    assert "sandbox API outage" in str(excinfo.value)


@pytest.mark.parametrize("recorded", [None, {"rules": []}])
async def test_recreate_hands_off_after_metadata_persists(
    recorded: dict[str, Any] | None,
) -> None:
    thread_id = "thread-recreate"
    old_sandbox = _backend("sandbox-old")
    new_sandbox = _backend("sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)
    expected: dict[str, Any] = {"sandbox_id": "sandbox-new"}
    if recorded is not None:
        expected["sandbox_base_proxy_config"] = recorded

    async def persist_metadata(**_kwargs: object) -> None:
        assert proxy.current is old_sandbox

    update = AsyncMock(side_effect=persist_metadata)

    with _wired(FakeProvider(created=new_sandbox), sandbox_id="sandbox-old", update=update):
        result = await _lifecycle(FakeCredentials(recorded=recorded)).recreate_for_thread(thread_id)

    assert result == ("sandbox-old", "sandbox-new")
    update.assert_awaited_once_with(thread_id=thread_id, metadata=expected)
    assert SANDBOX_BACKENDS[thread_id] is proxy
    assert proxy.current is new_sandbox


async def test_recreate_rejects_a_non_distinct_provider_result() -> None:
    thread_id = "thread-recreate-same-id"
    old_sandbox = _backend("sandbox-same")
    set_sandbox_backend(thread_id, old_sandbox)

    with (
        _wired(FakeProvider(created=_backend("sandbox-same")), sandbox_id="sandbox-same") as update,
        pytest.raises(RuntimeError, match="distinct sandbox"),
    ):
        await _lifecycle(FakeCredentials()).recreate_for_thread(thread_id)

    update.assert_not_awaited()
    assert SANDBOX_BACKENDS[thread_id].current is old_sandbox


@pytest.mark.parametrize(
    ("create_params", "expected_proxy_config"),
    [
        ({"snapshot_name": "python:latest", "proxy_config": {"rules": []}}, {"rules": []}),
        ({}, None),
    ],
    ids=["with-proxy-config", "clears-stale-proxy-config"],
)
async def test_reset_hands_off_after_metadata_persists(
    create_params: dict[str, Any], expected_proxy_config: dict[str, Any] | None
) -> None:
    thread_id = "thread-reset"
    old_sandbox = _backend("sandbox-old")
    new_sandbox = _backend("sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)
    credentials = FakeCredentials()

    async def persist_metadata(**_kwargs: object) -> None:
        assert proxy.current is old_sandbox
        assert credentials.bound == []

    update = AsyncMock(side_effect=persist_metadata)

    with (
        _wired(FakeProvider(), sandbox_id="sandbox-old", update=update),
        patch(
            f"{_MODULE}.create_langsmith_sandbox_from_params", AsyncMock(return_value=new_sandbox)
        ) as create,
    ):
        result = await _lifecycle(credentials).reset_for_thread(thread_id, create_params)

    assert result == ("sandbox-old", "sandbox-new")
    create.assert_awaited_once_with(create_params)
    assert credentials.installs == [("sandbox-new", expected_proxy_config)]
    assert credentials.bound == [thread_id]
    new_sandbox.aexecute.assert_awaited_once()
    update.assert_awaited_once_with(
        thread_id=thread_id,
        metadata={"sandbox_id": "sandbox-new", "sandbox_base_proxy_config": expected_proxy_config},
    )
    assert proxy.current is new_sandbox


@pytest.mark.parametrize(
    ("rebind", "expected_bound"),
    [("reset", []), ("recreate", ["thread-rebind-failure"])],
)
async def test_a_failed_metadata_write_keeps_the_old_binding(
    rebind: str, expected_bound: list[str]
) -> None:
    """Reset records credentials only after the write; recreate mints them first."""
    thread_id = "thread-rebind-failure"
    old_sandbox = _backend("sandbox-old")
    new_sandbox = _backend("sandbox-new")
    proxy = set_sandbox_backend(thread_id, old_sandbox)
    credentials = FakeCredentials()
    update = AsyncMock(side_effect=RuntimeError("metadata unavailable"))
    lifecycle = _lifecycle(credentials)

    with (
        _wired(
            FakeProvider(created=new_sandbox),
            sandbox_id="sandbox-old",
            update=update,
            reset_to=new_sandbox,
        ),
        pytest.raises(RuntimeError, match="metadata unavailable"),
    ):
        if rebind == "reset":
            await lifecycle.reset_for_thread(thread_id, {"proxy_config": {"rules": []}})
        else:
            await lifecycle.recreate_for_thread(thread_id)

    assert credentials.bound == expected_bound
    assert SANDBOX_BACKENDS[thread_id] is proxy
    assert proxy.current is old_sandbox


async def test_reset_rejects_other_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "modal")

    with pytest.raises(ValueError, match="only supported by the LangSmith"):
        await _lifecycle(FakeCredentials()).reset_for_thread("thread-reset", {})
