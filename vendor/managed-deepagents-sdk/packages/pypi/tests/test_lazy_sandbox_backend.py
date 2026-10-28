"""Unit tests for the lazy per-run managed sandbox backend."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import GlobResult
from langsmith.sandbox import ResourceNotFoundError, SandboxConnectionError

from managed_deepagents import runtime
from managed_deepagents._sandbox_proxy import SandboxProxyConfigSync


@pytest.fixture(autouse=True)
def _mock_proxy_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests cover backend dispatch; proxy behavior has separate tests."""
    monkeypatch.setattr(SandboxProxyConfigSync, "sync", AsyncMock())


def _make_sandbox() -> Any:
    return SimpleNamespace(
        kind="sandbox",
        options={},
    )


def _make_resolved_backend(backend_id: str) -> MagicMock:
    backend = MagicMock()
    backend.id = backend_id
    backend.ls.return_value = [{"name": "ok"}]
    backend.execute.side_effect = lambda command, **_kwargs: SimpleNamespace(
        exit_code=0,
        output=command,
    )
    return backend


def _create_backend(run_config: dict[str, Any] | None = None) -> Any:
    return runtime._managed_sandbox_backend(
        _make_sandbox(),
        run_config,
    )


def test_resolves_lazily_on_first_filesystem_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = _make_resolved_backend("sb-1")
    resolve = MagicMock(return_value=resolved)
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)

    backend = _create_backend({"configurable": {"thread_id": "t-lazy"}})

    assert resolve.call_count == 0
    # Non-empty pending id so deepagents keeps execute (sandbox protocol).
    assert backend.id == "pending:thread:t-lazy"

    assert backend.ls("/workspace") == [{"name": "ok"}]
    assert resolve.call_count == 1
    resolved.ls.assert_called_once_with("/workspace")
    assert backend.id == "sb-1"


def test_exposes_non_empty_id_before_resolve_and_never_throws_without_thread_id() -> None:
    with_thread = _create_backend({"configurable": {"thread_id": "t-id"}})
    assert with_thread.id == "pending:thread:t-id"

    unbound = _create_backend()
    assert unbound.id == "pending:sandbox"


def test_reuses_resolved_backend_for_same_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = _make_resolved_backend("sb-reuse")
    resolve = MagicMock(return_value=resolved)
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)

    backend = _create_backend().bind_runtime({"configurable": {"thread_id": "t-1"}})
    assert resolve.call_count == 0

    first = backend.execute("echo 1")
    second = backend.execute("echo 2")

    assert first.output == "echo 1"
    assert second.output == "echo 2"
    assert resolve.call_count == 1


def test_bind_runtime_freezes_distinct_configurables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[dict[str, Any] | None] = []

    def fake_resolve(
        _sandbox: Any,
        config: dict[str, Any] | None,
        **_kwargs: Any,
    ) -> MagicMock:
        configurable = None
        if isinstance(config, dict):
            raw = config.get("configurable")
            if isinstance(raw, dict):
                configurable = raw
        configs.append(configurable)
        thread_id = configurable.get("thread_id") if configurable else "none"
        return _make_resolved_backend(f"sb-{thread_id}")

    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", fake_resolve)

    root = _create_backend()
    a = root.bind_runtime({"configurable": {"thread_id": "thread-a"}})
    b = root.bind_runtime({"configurable": {"thread_id": "thread-b"}})

    a.execute("a")
    b.execute("b")

    thread_ids = {
        configurable.get("thread_id") for configurable in configs if configurable is not None
    }
    assert thread_ids == {"thread-a", "thread-b"}


def test_bind_runtime_defers_resolve_failure_until_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve = MagicMock(side_effect=RuntimeError("setup failed"))
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)

    backend = _create_backend().bind_runtime({"configurable": {"thread_id": "t-bind"}})

    assert backend.id == "pending:thread:t-bind"
    assert resolve.call_count == 0

    # Binding does not resolve; the first operation surfaces the setup error.
    with pytest.raises(RuntimeError, match="setup failed"):
        backend.execute("echo")
    assert resolve.call_count == 1


def test_bind_runtime_allows_retry_after_resolve_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = _make_resolved_backend("sb-after-retry")
    resolve = MagicMock(
        side_effect=[RuntimeError("setup failed"), resolved],
    )
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)

    backend = _create_backend().bind_runtime({"configurable": {"thread_id": "t-retry"}})
    assert backend.id == "pending:thread:t-retry"
    with pytest.raises(RuntimeError, match="setup failed"):
        backend.execute("echo first attempt")

    result = backend.execute("echo ok")
    assert result.output == "echo ok"
    assert backend.id == "sb-after-retry"
    assert resolve.call_count == 2


@pytest.mark.parametrize(
    "path",
    ["/", None, "", "  /  ", "//", "/.", "/..", "/./..", "/workspace/../.."],
)
def test_refuses_root_glob_without_resolving_a_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    path: str | None,
) -> None:
    resolved = _make_resolved_backend("sb-root-glob")
    resolve = MagicMock(return_value=resolved)
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)

    result = _create_backend().glob("**/hello.txt", path)

    assert isinstance(result, GlobResult)
    assert result.error is not None
    assert "cannot glob the sandbox root" in result.error
    assert result.matches == []
    # A root glob would `find -L /` and buffer the whole rootfs in this heap.
    assert resolve.call_count == 0
    assert resolved.glob.call_count == 0


@pytest.mark.parametrize("path", ["/", "/..", "/./..", "/workspace/../.."])
def test_refuses_root_glob_on_the_async_path(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    resolved = _make_resolved_backend("sb-root-aglob")
    resolve = MagicMock(return_value=resolved)
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)

    backend = _create_backend()

    async def _run() -> None:
        result = await backend.aglob("**/hello.txt", path)
        assert isinstance(result, GlobResult)
        assert result.error is not None
        assert "cannot glob the sandbox root" in result.error

    asyncio.run(_run())

    assert resolve.call_count == 0


def test_passes_a_directory_glob_through_to_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = _make_resolved_backend("sb-dir-glob")
    resolved.glob.return_value = GlobResult(matches=[])
    resolve = MagicMock(return_value=resolved)
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)

    backend = _create_backend().bind_runtime({"configurable": {"thread_id": "t-glob"}})

    assert backend.glob("**/hello.txt", "/workspace") == GlobResult(matches=[])
    resolved.glob.assert_called_once_with("**/hello.txt", "/workspace")


def test_does_not_retry_non_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = _make_resolved_backend("sb-perm")
    resolved.execute.side_effect = RuntimeError("permission denied")
    resolve = MagicMock(return_value=resolved)
    client = MagicMock()
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)
    monkeypatch.setattr(runtime, "SandboxClient", client)

    backend = _create_backend().bind_runtime({"configurable": {"thread_id": "t-perm"}})

    with pytest.raises(RuntimeError, match="permission denied"):
        backend.execute("rm -rf /")

    client.assert_not_called()
    assert resolve.call_count == 1


@pytest.mark.parametrize("operation", ["adownload_files", "aupload_files"])
def test_async_file_transfers_resolve_sandbox_off_event_loop(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    resolution_threads: list[int] = []

    async def download(_paths: list[str]) -> list[object]:
        return [SimpleNamespace(content=b"image", error=None)]

    async def upload(_files: list[tuple[str, bytes]]) -> list[object]:
        return [SimpleNamespace(error=None)]

    def resolve(*_args: object, **_kwargs: object) -> object:
        resolution_threads.append(threading.get_ident())
        return SimpleNamespace(adownload_files=download, aupload_files=upload)

    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)
    config = {"configurable": {"thread_id": "async-files"}}
    source = runtime._managed_sandbox_backend(SimpleNamespace(kind="sandbox", options={}), config)
    backend = source.bind_runtime(config)

    async def run() -> None:
        event_loop_thread = threading.get_ident()
        assert resolution_threads == []
        if operation == "adownload_files":
            assert (await backend.adownload_files(["/workspace/image.png"]))[0].content == b"image"
        else:
            await backend.aupload_files([("/workspace/image.png", b"image")])
        assert resolution_threads
        assert event_loop_thread not in resolution_threads

    asyncio.run(run())


def test_helper_does_not_recover_its_leads_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = _make_resolved_backend("sb-lead")
    failure = ResourceNotFoundError("missing", "sandbox")
    failing.execute.side_effect = failure
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", MagicMock(return_value=failing))
    recovery = MagicMock()
    monkeypatch.setattr(runtime, "_recover_managed_sandbox", recovery)
    configurable = {"thread_id": "t-helper", "deepagents_parent": {"thread_id": "t-lead"}}
    backend = _create_backend().bind_runtime({"configurable": configurable})
    token = runtime._VERIFIED_LEAD_THREAD.set("t-lead")

    try:
        with pytest.raises(ResourceNotFoundError):
            backend.execute("ls")
    finally:
        runtime._VERIFIED_LEAD_THREAD.reset(token)
    recovery.assert_not_called()


@pytest.mark.parametrize(
    ("method", "failure", "recover"),
    [
        ("execute", RuntimeError("HTTP 502: Bad Gateway"), False),
        ("read", RuntimeError("sandbox not found"), False),
        ("aexecute", RuntimeError("Command stream ended before 'started' message"), False),
        ("execute", ResourceNotFoundError("missing", "sandbox"), True),
        ("aread", ResourceNotFoundError("missing", "file"), True),
    ],
)
def test_operation_failure_is_not_replayed(
    monkeypatch: pytest.MonkeyPatch, method: str, failure: Exception, recover: bool
) -> None:
    original = _make_resolved_backend("old")
    replacement = _make_resolved_backend("new")
    mock = AsyncMock if method.startswith("a") else MagicMock
    operation = mock(side_effect=[failure, "ok"])
    setattr(original, method, operation)
    setattr(replacement, method, mock(return_value="ok"))
    resolve = MagicMock(return_value=original)
    recovery = MagicMock(return_value=replacement)
    monkeypatch.setattr(runtime, "_resolve_managed_sandbox", resolve)
    monkeypatch.setattr(runtime, "_recover_managed_sandbox", recovery)
    backend = _create_backend({"configurable": {"thread_id": "t-failure"}})

    def invoke(value: str) -> object:
        result = getattr(backend, method)(value)
        return asyncio.run(result) if method.startswith("a") else result

    with pytest.raises(type(failure)) as caught:
        invoke("first")
    assert caught.value is failure
    operation.assert_called_once()
    getattr(replacement, method).assert_not_called()
    assert recovery.call_count == int(recover)
    assert invoke("next") == "ok"
    assert backend.id == ("new" if recover else "old")
    resolve.assert_called_once()


@pytest.mark.parametrize("status", [404, 502])
def test_websocket_recovery_requires_404(status: int) -> None:
    failure = SandboxConnectionError("WebSocket handshake rejected")
    cause = RuntimeError("handshake failed")
    cause.response = SimpleNamespace(status_code=status)
    failure.__cause__ = cause
    assert runtime._is_sandbox_not_found_error(failure) is (status == 404)
