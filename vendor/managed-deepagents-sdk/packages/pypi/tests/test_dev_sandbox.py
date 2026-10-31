"""Unit tests for per-thread LangSmith sandbox resolution."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from langsmith import tracing_context
from langsmith.run_helpers import get_tracing_context
from langsmith.sandbox import ResourceAlreadyExistsError, ResourceNotFoundError

from managed_deepagents import _sandbox_proxy, define_sandbox, runtime
from managed_deepagents.sandbox import SandboxDefinition

RECIPE = "test-recipe-snapshot"


def _langsmith(**options: Any) -> Any:
    return define_sandbox(**options)


class LangSmithSandbox:
    """Stand-in for DeepAgents' LangSmithSandbox backend."""

    instances: ClassVar[list[LangSmithSandbox]] = []

    def __init__(self, *, sandbox: Any) -> None:
        self.sandbox = sandbox
        self._default_timeout = 30 * 60
        self.execute_calls: list[str] = []
        self.upload_calls: list[list[tuple[str, bytes]]] = []
        LangSmithSandbox.instances.append(self)

    @property
    def id(self) -> str:
        return str(self.sandbox["name"])

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[Any]:
        self.upload_calls.append(files)
        return [types.SimpleNamespace(path=path) for path, _ in files]

    def execute(self, command: str) -> Any:
        self.execute_calls.append(command)
        return types.SimpleNamespace(exit_code=0, output="")

    async def aexecute(self, command: str) -> Any:
        return self.execute(command)


class _FakeSandboxClient:
    """Stand-in for ``langsmith.sandbox.SandboxClient``.

    Driven by the dict the ``fake_sandbox_client`` fixture installs: each method
    records its calls and pops any scripted error/result the test queued.
    """

    state: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        api_endpoint: str | None = None,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.state["client_api_endpoints"].append(api_endpoint)
        self.state["client_api_keys"].append(api_key)
        self.state["client_headers"].append(headers)

    def create_sandbox(self, **kwargs: Any) -> Any:
        self.state["create_calls"].append(kwargs)
        _raise_scripted(self.state["errors"])
        results = self.state["results"]
        if results:
            return results.pop(0)
        return {"name": f"sandbox-{len(self.state['create_calls'])}"}

    def get_sandbox(self, name: str) -> Any:
        self.state["get_calls"].append(name)
        _raise_scripted(self.state["get_errors"])
        by_name = self.state["get_by_name"]
        if name in by_name:
            return by_name[name]
        results = self.state["get_results"]
        if results:
            return results.pop(0)
        raise ResourceNotFoundError("sandbox not found")

    def start_sandbox(self, name: str) -> Any:
        self.state["start_calls"].append(name)
        results = self.state["start_results"]
        return results.pop(0) if results else None

    def delete_sandbox(self, name: str) -> None:
        self.state["delete_calls"].append(name)
        _raise_scripted(self.state["delete_errors"])
        self.state["get_by_name"].pop(name, None)


def _raise_scripted(errors: list[Exception | None]) -> None:
    """Raise the next queued error, if the test queued one for this call."""
    if not errors:
        return
    error = errors.pop(0)
    if error is not None:
        raise error


@pytest.fixture
def fake_sandbox_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(_sandbox_proxy, "patch_sandbox_proxy_config", lambda *_args: None)
    for name in (
        "LANGSMITH_API_KEY",
        runtime.WORKSPACE_ID_ENV,
        "MDA_SANDBOX_API_ENDPOINT",
        "MDA_DEPLOYMENT_NAME",
        "LANGSMITH_DEPLOYMENT_NAME",
        "MDA_LOCAL_DEV",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(runtime.RECIPE_SNAPSHOT_ENV, RECIPE)
    state: dict[str, Any] = {
        "client_api_endpoints": [],
        "client_api_keys": [],
        "client_headers": [],
        "create_calls": [],
        "results": [],
        "errors": [],
        "get_calls": [],
        "get_results": [],
        "get_by_name": {},
        "get_errors": [],
        "start_calls": [],
        "start_results": [],
        "delete_calls": [],
        "delete_errors": [],
    }
    monkeypatch.setattr(_FakeSandboxClient, "state", state)
    monkeypatch.setattr(runtime, "SandboxClient", _FakeSandboxClient)
    monkeypatch.setattr(runtime, "LangSmithSandbox", LangSmithSandbox)
    return state


@pytest.fixture(autouse=True)
def _clean_runtime() -> Iterator[None]:
    runtime._managed_sandboxes.clear()
    runtime._managed_sandbox_key_locks.clear()
    runtime._invalidated_sandbox_names.clear()
    LangSmithSandbox.instances.clear()
    yield
    runtime._managed_sandboxes.clear()
    runtime._managed_sandbox_key_locks.clear()
    runtime._invalidated_sandbox_names.clear()


def _resolve(
    sandbox: Any,
    thread_id: str = "t-1",
    *,
    selection: runtime._SandboxSelection | None = None,
) -> Any:
    return runtime._resolve_managed_sandbox(
        sandbox,
        {"configurable": {"thread_id": thread_id, "assistant_id": "assistant-1"}},
        selection=selection,
    )


def test_creates_langsmith_sandbox_once_per_thread_scope(
    fake_sandbox_client: dict[str, Any],
) -> None:
    sandbox = _langsmith(
        idle_ttl_seconds=600,
        default_timeout=30,
    )

    first = _resolve(sandbox)
    second = _resolve(sandbox)

    assert first is second
    assert fake_sandbox_client["create_calls"] == [
        {"idle_ttl_seconds": 600, "snapshot_name": RECIPE}
    ]
    assert fake_sandbox_client["client_api_keys"] == [None]
    assert isinstance(first, LangSmithSandbox)
    assert first._default_timeout == 30


def test_reuses_equivalent_dynamic_sandbox_selections(
    fake_sandbox_client: dict[str, Any],
) -> None:
    first = _langsmith(default_timeout=100, idle_ttl_seconds=60)
    equivalent = _langsmith(idle_ttl_seconds=60, default_timeout=100)

    first_backend = _resolve(
        first,
        selection=runtime._sandbox_selection(first),
    )
    second_backend = _resolve(
        equivalent,
        selection=runtime._sandbox_selection(equivalent),
    )

    assert first_backend is second_backend
    assert len(fake_sandbox_client["create_calls"]) == 1


def test_changed_dynamic_sandbox_selection_keeps_the_live_box(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    first = _langsmith(snapshot_name="snapshot-a", idle_ttl_seconds=60)
    second = _langsmith(snapshot_name="snapshot-b", idle_ttl_seconds=120)

    first_backend = _resolve(first, selection=runtime._sandbox_selection(first))
    with caplog.at_level(logging.WARNING, logger="mda"):
        second_backend = _resolve(second, selection=runtime._sandbox_selection(second))

    assert first_backend is second_backend
    assert fake_sandbox_client["create_calls"] == [
        {
            "idle_ttl_seconds": 60,
            "name": runtime._managed_sandbox_name("my-agent", "thread:t-1"),
            "snapshot_name": "snapshot-a",
        }
    ]
    assert caplog.messages == [
        "[mda] sandbox options changed on thread t-1; they apply only when a new sandbox is created"
    ]


def test_selection_mounts_key_the_box() -> None:
    config = {"configurable": {"thread_id": "t-1"}}

    def key(**options: object) -> str:
        selection = runtime._sandbox_selection(SandboxDefinition(options))
        return runtime._thread_scope_key(config, selection)

    alice = key(snapshot_name="a", mount_config={"mounts": [{"bucket": "alice"}]})
    assert key(snapshot_name="a") == key(snapshot_name="b") == "thread:t-1"
    assert alice == key(snapshot_name="b", mount_config={"mounts": [{"bucket": "alice"}]})
    assert alice != key(snapshot_name="a", mount_config={"mounts": [{"bucket": "bob"}]})
    assert alice.startswith("thread:t-1:mounts:")


def _proxied(value: str) -> dict[str, object]:
    return {
        "proxy_config": {"rules": [{"name": "static", "headers": [{"name": "X", "value": value}]}]}
    }


@pytest.mark.parametrize(
    ("same", "first", "second"),
    [
        (False, {"snapshot_id": "a"}, {"snapshot_id": "b"}),
        (False, {"snapshot_name": "a"}, {"snapshot_name": "b"}),
        (False, {"docker_image": "a"}, {"docker_image": "b"}),
        (False, {"registry": {"url": "a"}}, {"registry": {"url": "b"}}),
        (False, {"vcpus": 1}, {"vcpus": 2}),
        (False, {"mem_bytes": 1}, {"mem_bytes": 2}),
        (False, {"fs_capacity_bytes": 1}, {"fs_capacity_bytes": 2}),
        (False, {"mount_config": {"mounts": []}}, {"mount_config": {"mounts": [{}]}}),
        (
            True,
            {"default_timeout": 1, "idle_ttl_seconds": 1},
            {"default_timeout": 2, "idle_ttl_seconds": 2},
        ),
        (True, _proxied("a"), _proxied("b")),
        (True, {"registry": {"username": "a"}}, {"registry": {"username": "b"}}),
        (True, {"mount_config": {"auth": "a"}}, {"mount_config": {"auth": "b"}}),
        (True, {"vcpus": None}, {}),
    ],
)
def test_selection_fingerprint_hashes_only_identity_options(
    same: bool, first: dict[str, object], second: dict[str, object]
) -> None:
    fingerprint = runtime._sandbox_selection_fingerprint
    assert (fingerprint(SandboxDefinition(first)) == fingerprint(SandboxDefinition(second))) is same


@pytest.mark.parametrize("live", [True, False])
def test_looks_up_new_then_options_keyed_names(
    fake_sandbox_client: dict[str, Any], monkeypatch: pytest.MonkeyPatch, live: bool
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    sandbox = _langsmith(snapshot_name="a", default_timeout=100)
    selection = runtime._sandbox_selection(sandbox)
    options_key = f"thread:t-1:sandbox:{selection.fingerprint}"
    names = [
        runtime._managed_sandbox_name("my-agent", "thread:t-1"),
        runtime._managed_sandbox_name("my-agent", options_key),
        runtime._legacy_managed_sandbox_name("my-agent", "thread:t-1", {"snapshot_name": "a"}),
    ]
    box = {"name": names[1], "status": "ready"}
    if live:
        fake_sandbox_client["get_by_name"][names[1]] = box

    backend = _resolve(sandbox, selection=selection)

    assert fake_sandbox_client["get_calls"] == (names[:2] if live else names)
    if live:
        assert fake_sandbox_client["create_calls"] == []
        assert backend.sandbox == box
    else:
        assert fake_sandbox_client["create_calls"] == [{"name": names[0], "snapshot_name": "a"}]


def test_discovered_sandbox_reuse_remains_thread_scoped(
    fake_sandbox_client: dict[str, Any],
) -> None:
    first = _resolve(_langsmith(default_timeout=100))
    second = _resolve(_langsmith(default_timeout=200))

    assert first is second
    assert len(fake_sandbox_client["create_calls"]) == 1
    assert LangSmithSandbox.instances[0]._default_timeout == 100


def test_cold_create_does_not_hold_process_wide_sandbox_lock(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold create/provision must not serialize other Host workers.

    Integration failures showed PII/echo invokes wedging while a sibling
    sandbox provision held ``_managed_sandbox_lock`` for tens of seconds.
    Same-key work is serialized via ``_managed_sandbox_key_locks`` instead.
    """
    del fake_sandbox_client  # fixture still installs the stub client
    sandbox = _langsmith()
    real_create = runtime._create_langsmith_sandbox
    unlocked = threading.Event()

    def create_outside_global_lock(*args: Any, **kwargs: Any) -> Any:
        acquired = runtime._managed_sandbox_lock.acquire(blocking=False)
        assert acquired, "cold create held _managed_sandbox_lock"
        runtime._managed_sandbox_lock.release()
        unlocked.set()
        return real_create(*args, **kwargs)

    monkeypatch.setattr(runtime, "_create_langsmith_sandbox", create_outside_global_lock)
    _resolve(sandbox)
    assert unlocked.is_set()


def test_concurrent_same_key_resolves_share_one_cold_create(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two workers for one scope must not double-create / adopt mid-setup."""
    sandbox = _langsmith()
    real_create = runtime._create_langsmith_sandbox
    entered_create = threading.Event()
    release_create = threading.Event()
    create_calls = 0
    create_lock = threading.Lock()

    def gated_create(*args: Any, **kwargs: Any) -> Any:
        nonlocal create_calls
        with create_lock:
            create_calls += 1
        entered_create.set()
        assert release_create.wait(timeout=5), "create was not released"
        return real_create(*args, **kwargs)

    monkeypatch.setattr(runtime, "_create_langsmith_sandbox", gated_create)

    results: list[Any] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(_resolve(sandbox, thread_id="shared"))
        except BaseException as err:  # noqa: BLE001 - surface in parent
            errors.append(err)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    # Owner enters create; waiter blocks on the per-key lock (not in create).
    assert entered_create.wait(timeout=5)
    time.sleep(0.2)
    assert create_calls == 1
    release_create.set()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(results) == 2
    assert results[0] is results[1]
    assert create_calls == 1
    assert len(fake_sandbox_client["create_calls"]) == 1


def test_concurrent_different_keys_create_in_parallel(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unrelated scope keys must not wait on each other's cold create."""
    sandbox = _langsmith()
    real_create = runtime._create_langsmith_sandbox
    both_inside = threading.Barrier(2)
    release_create = threading.Event()

    def parallel_create(*args: Any, **kwargs: Any) -> Any:
        both_inside.wait(timeout=5)
        assert release_create.wait(timeout=5)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(runtime, "_create_langsmith_sandbox", parallel_create)

    errors: list[BaseException] = []

    def worker(thread_id: str) -> None:
        try:
            _resolve(sandbox, thread_id=thread_id)
        except BaseException as err:  # noqa: BLE001 - surface in parent
            errors.append(err)

    threads = [
        threading.Thread(target=worker, args=("t-a",)),
        threading.Thread(target=worker, args=("t-b",)),
    ]
    for thread in threads:
        thread.start()
    # Barrier trips only if both creates run concurrently.
    release_create.set()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(fake_sandbox_client["create_calls"]) == 2


def test_creates_distinct_sandboxes_for_distinct_threads(
    fake_sandbox_client: dict[str, Any],
) -> None:
    sandbox = _langsmith()

    first = _resolve(sandbox, thread_id="t-1")
    second = _resolve(sandbox, thread_id="t-2")

    assert first is not second
    assert [backend.id for backend in LangSmithSandbox.instances] == [
        "sandbox-1",
        "sandbox-2",
    ]
    assert len(fake_sandbox_client["create_calls"]) == 2


@pytest.mark.parametrize("snapshot_key", ["snapshot_id", "snapshot_name"])
@pytest.mark.parametrize("recipe", [RECIPE, None])
def test_dynamic_snapshot_reaches_sandbox_creation(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    snapshot_key: str,
    recipe: str | None,
) -> None:
    if recipe is None:
        monkeypatch.delenv(runtime.RECIPE_SNAPSHOT_ENV, raising=False)
    sandbox = _langsmith(**{snapshot_key: "workspace-snapshot"}, idle_ttl_seconds=600)

    runtime._resolve_managed_sandbox(
        sandbox,
        {"configurable": {"thread_id": "t-1"}},
        selection=runtime._sandbox_selection(sandbox),
    )

    assert fake_sandbox_client["create_calls"] == [
        {snapshot_key: "workspace-snapshot", "idle_ttl_seconds": 600}
    ]


def test_clones_recipe_snapshot_from_env(
    fake_sandbox_client: dict[str, Any],
) -> None:
    sandbox = _langsmith(snapshot_name="author-bake-base")

    _resolve(sandbox)

    assert fake_sandbox_client["create_calls"] == [{"snapshot_name": RECIPE}]


def test_scopes_sandbox_client_to_workspace_id(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv(runtime.WORKSPACE_ID_ENV, "ws-1")

    _resolve(_langsmith())

    assert fake_sandbox_client["client_api_keys"] == ["test-key"]
    assert fake_sandbox_client["client_headers"] == [{"X-Tenant-Id": "ws-1"}]


def test_uses_deploy_owned_sandbox_api_endpoint(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_SANDBOX_API_ENDPOINT", "https://self-hosted.example/v2/sandboxes")

    _resolve(_langsmith())

    assert fake_sandbox_client["client_api_endpoints"] == [
        "https://self-hosted.example/v2/sandboxes"
    ]


def test_rejects_unsafe_deploy_owned_sandbox_api_endpoint(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        runtime.SANDBOX_API_ENDPOINT_ENV,
        "https://user:password@self-hosted.example/v2/sandboxes",
    )

    with pytest.raises(RuntimeError, match="must be an HTTP\\(S\\) URL without credentials"):
        _resolve(_langsmith())


def test_strips_author_bake_bases_from_create_options(
    fake_sandbox_client: dict[str, Any],
) -> None:
    # Bypass authoring mutual-exclusion to assert runtime stripping.
    sandbox = types.SimpleNamespace(
        kind="sandbox",
        options={
            "snapshot_name": "base",
            "snapshot_id": "snap-1",
            "docker_image": "python:3.12-slim",
            "idle_ttl_seconds": 600,
        },
    )

    _resolve(sandbox)

    assert fake_sandbox_client["create_calls"] == [
        {"idle_ttl_seconds": 600, "snapshot_name": RECIPE}
    ]


def test_creates_from_platform_default_without_recipe_snapshot_env(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(runtime.RECIPE_SNAPSHOT_ENV, raising=False)
    sandbox = _langsmith()

    _resolve(sandbox)

    assert fake_sandbox_client["create_calls"] == [{}]


def test_failed_provisioning_is_not_cached(fake_sandbox_client: dict[str, Any]) -> None:
    fake_sandbox_client["errors"].append(RuntimeError("provider down"))
    sandbox = _langsmith()

    with pytest.raises(RuntimeError, match="provider down"):
        _resolve(sandbox)
    assert _resolve(sandbox).id == "sandbox-2"
    assert len(fake_sandbox_client["create_calls"]) == 2


def test_thread_scope_requires_thread_id(fake_sandbox_client: dict[str, Any]) -> None:
    sandbox = _langsmith()

    with pytest.raises(
        RuntimeError,
        match=r"managed sandboxes require configurable\.thread_id",
    ):
        runtime._resolve_managed_sandbox(sandbox, {"configurable": {}})


def test_managed_sandbox_name_is_stable_and_prefixed_by_deployment() -> None:
    first = runtime._managed_sandbox_name("my-agent", "thread:t-1")

    assert first == runtime._managed_sandbox_name("my-agent", "thread:t-1")
    assert first.startswith("my-agent--")


def test_managed_sandbox_name_differs_per_scope() -> None:
    assert runtime._managed_sandbox_name("my-agent", "thread:t-1") != runtime._managed_sandbox_name(
        "my-agent", "thread:t-2"
    )
    assert runtime._managed_sandbox_name("my-agent", "agent:a-1") != runtime._managed_sandbox_name(
        "my-agent", "thread:a-1"
    )


def test_sandbox_name_cannot_be_confused_with_a_longer_deployment_name() -> None:
    # The doubled separator is what keeps ``my-agent--`` from matching a
    # ``my-agent-staging`` sandbox during teardown.
    assert not runtime._managed_sandbox_name("my-agent-staging", "thread:t-1").startswith(
        "my-agent--"
    )


def test_managed_sandbox_name_preserves_existing_primary_name_when_it_fits() -> None:
    deployment = "mda-integ-sandbox-recipe-ts-pr-339-4d66e3"
    name = runtime._managed_sandbox_name(
        deployment,
        "thread:t-1",
    )

    assert len(name) <= 63
    assert re.fullmatch(rf"{deployment}--[0-9a-f]{{12}}", name)


def test_names_sandboxes_after_the_deployment(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    sandbox = _langsmith()

    _resolve(sandbox)

    assert fake_sandbox_client["create_calls"] == [
        {
            "name": runtime._managed_sandbox_name("my-agent", "thread:t-1"),
            "snapshot_name": RECIPE,
        }
    ]


def test_prefers_the_platform_deployment_name(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "injected")
    monkeypatch.setenv("LANGSMITH_DEPLOYMENT_NAME", "platform")
    sandbox = _langsmith()

    _resolve(sandbox)

    assert fake_sandbox_client["create_calls"] == [
        {
            "name": runtime._managed_sandbox_name("platform", "thread:t-1"),
            "snapshot_name": RECIPE,
        }
    ]


def test_leaves_sandbox_unnamed_without_a_deployment(
    fake_sandbox_client: dict[str, Any],
) -> None:
    _resolve(_langsmith())

    assert fake_sandbox_client["create_calls"] == [{"snapshot_name": RECIPE}]
    assert fake_sandbox_client["get_calls"] == []


def test_names_mda_dev_sandboxes_after_the_machine_and_project(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MDA_LOCAL_DEV", "1")
    monkeypatch.chdir(tmp_path)

    _resolve(_langsmith())
    deployment = runtime._managed_deployment_name()
    monkeypatch.chdir(tmp_path.parent)

    assert deployment is not None
    assert re.fullmatch(r"mda-dev-[0-9a-f]{8}", deployment)
    assert runtime._managed_deployment_name() != deployment
    assert runtime._managed_deployment_name({"MDA_LOCAL_DEV": "1"}) is None
    assert fake_sandbox_client["create_calls"][0]["name"] == runtime._managed_sandbox_name(
        deployment, "thread:t-1"
    )


def test_adopts_the_deployments_existing_sandbox(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    fake_sandbox_client["get_results"].append(
        types.SimpleNamespace(name="sandbox-existing", status="ready")
    )
    sandbox = _langsmith()

    backend = _resolve(sandbox)

    assert fake_sandbox_client["get_calls"] == [
        runtime._managed_sandbox_name("my-agent", "thread:t-1")
    ]
    assert fake_sandbox_client["create_calls"] == []
    assert backend.sandbox.name == "sandbox-existing"


def test_restarts_a_stopped_sandbox_instead_of_replacing_it(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    fake_sandbox_client["get_results"].append(
        types.SimpleNamespace(name="sandbox-existing", status="stopped")
    )
    fake_sandbox_client["start_results"].append(
        types.SimpleNamespace(name="sandbox-existing", status="ready")
    )

    backend = _resolve(_langsmith())

    assert fake_sandbox_client["start_calls"] == [
        runtime._managed_sandbox_name("my-agent", "thread:t-1")
    ]
    assert fake_sandbox_client["create_calls"] == []
    assert backend.sandbox.name == "sandbox-existing"


def test_creates_a_fresh_sandbox_when_the_existing_one_is_unusable(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    fake_sandbox_client["get_results"].append(
        types.SimpleNamespace(name="sandbox-existing", status="failed")
    )

    _resolve(_langsmith())

    assert fake_sandbox_client["create_calls"] == [
        {
            "name": runtime._managed_sandbox_name("my-agent", "thread:t-1"),
            "snapshot_name": RECIPE,
        }
    ]


def test_adopts_the_winner_when_a_concurrent_container_wins_the_create_race(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    # First lookup finds nothing, the create loses the race, the retry adopts.
    fake_sandbox_client["get_errors"].append(ResourceNotFoundError("not found"))
    fake_sandbox_client["get_errors"].append(ResourceNotFoundError("not found"))
    fake_sandbox_client["errors"].append(RuntimeError("HTTP 409: sandbox already exists"))
    fake_sandbox_client["get_results"].append(
        types.SimpleNamespace(name="sandbox-winner", status="ready")
    )

    backend = _resolve(_langsmith())

    assert backend.sandbox.name == "sandbox-winner"
    assert len(fake_sandbox_client["create_calls"]) == 1


def test_falls_back_to_a_unique_name_that_keeps_the_deployment_prefix(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = "mda-integ-sandbox-recipe-ts-pr-339-4d66e3"
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", deployment)
    # The name is taken but its holder is never adoptable.
    fake_sandbox_client["get_results"].extend(
        [
            types.SimpleNamespace(name="sandbox-existing", status="deleting"),
            types.SimpleNamespace(name="sandbox-existing", status="deleting"),
        ]
    )
    fake_sandbox_client["errors"].append(RuntimeError("HTTP 409: sandbox already exists"))

    _resolve(_langsmith())

    expected = runtime._managed_sandbox_name(deployment, "thread:t-1")
    fallback = fake_sandbox_client["create_calls"][1]["name"]
    assert not fallback.startswith(f"{expected}-")
    assert re.search(r"-[0-9a-f]{8}--[0-9a-f]{12}-[0-9a-f]{8}$", fallback)
    assert len(fallback) <= 63


def test_a_non_conflict_create_failure_is_not_retried(
    fake_sandbox_client: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MDA_DEPLOYMENT_NAME", "my-agent")
    fake_sandbox_client["errors"].append(RuntimeError("quota exceeded"))

    with pytest.raises(RuntimeError, match="quota exceeded"):
        _resolve(_langsmith())
    assert len(fake_sandbox_client["create_calls"]) == 1


def _run_config(thread_id: str = "t-1") -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "assistant_id": "assistant-1"}}


def test_managed_backend_defers_langsmith_creation_until_operation(
    fake_sandbox_client: dict[str, Any],
) -> None:
    sandbox = _langsmith()

    backend = runtime._managed_sandbox_backend(sandbox, _run_config())

    assert backend.id == "pending:thread:t-1"
    assert fake_sandbox_client["create_calls"] == []

    backend.execute("pwd")

    assert fake_sandbox_client["create_calls"] == [{"snapshot_name": RECIPE}]
    assert LangSmithSandbox.instances[0].execute_calls == ["pwd"]
    assert backend.id == "sandbox-1"


def test_selected_sandbox_is_snapshotted_before_lazy_provisioning(
    fake_sandbox_client: dict[str, Any],
) -> None:
    selected = _langsmith(default_timeout=60, idle_ttl_seconds=100)
    snapshot = runtime._select_run_sandbox({"sandbox": selected}, None)
    assert snapshot is not None
    backend = runtime._managed_sandbox_backend(
        snapshot,
        _run_config(),
        selection=runtime._sandbox_selection(snapshot),
    )

    selected.options["default_timeout"] = 120
    selected.options["idle_ttl_seconds"] = 200
    backend.execute("pwd")

    equivalent = _langsmith(default_timeout=60, idle_ttl_seconds=100)
    equivalent_snapshot = runtime._select_run_sandbox({"sandbox": equivalent}, None)
    assert equivalent_snapshot is not None
    equivalent_backend = runtime._managed_sandbox_backend(
        equivalent_snapshot,
        _run_config(),
        selection=runtime._sandbox_selection(equivalent_snapshot),
    )
    equivalent_backend.execute("pwd")

    assert fake_sandbox_client["create_calls"] == [
        {"idle_ttl_seconds": 100, "snapshot_name": RECIPE}
    ]
    assert [instance._default_timeout for instance in LangSmithSandbox.instances] == [60]


def test_managed_backend_async_operation_offloads_first_creation(
    fake_sandbox_client: dict[str, Any],
) -> None:
    sandbox = _langsmith()
    backend = runtime._managed_sandbox_backend(sandbox, _run_config())

    asyncio.run(backend.aexecute("pwd"))

    assert fake_sandbox_client["create_calls"] == [{"snapshot_name": RECIPE}]
    assert LangSmithSandbox.instances[0].execute_calls == ["pwd"]


@pytest.mark.parametrize("sandbox_id", ["old", "new"])
def test_recovery_adopts_existing_sandbox_without_restart(
    fake_sandbox_client: dict[str, Any], sandbox_id: str
) -> None:
    fake_sandbox_client["results"].append({"name": "original", "id": "old"})
    sandbox = _langsmith()
    backend = _resolve(sandbox)
    fake_sandbox_client["get_results"].append(
        {"name": "original", "id": sandbox_id, "status": "stopped"}
    )
    recovered = runtime._recover_managed_sandbox(
        sandbox, {"configurable": {"thread_id": "t-1"}}, backend
    )
    assert recovered.sandbox["id"] == sandbox_id
    if sandbox_id == "old":
        assert recovered is backend
    assert _resolve(sandbox) is recovered
    assert len(fake_sandbox_client["create_calls"]) == 1
    assert fake_sandbox_client["start_calls"] == []
    assert fake_sandbox_client["delete_calls"] == []


@pytest.mark.parametrize("conflict", [False, True])
def test_recovery_replaces_deleted_sandbox_under_same_name(
    fake_sandbox_client: dict[str, Any], conflict: bool
) -> None:
    sandbox = _langsmith()
    backend = _resolve(sandbox)
    name = backend.sandbox["name"]
    fake_sandbox_client["get_errors"].append(ResourceNotFoundError("missing", "sandbox"))
    fake_sandbox_client["results"].append({"name": name, "id": "new"})
    if conflict:
        fake_sandbox_client["errors"].append(ResourceAlreadyExistsError("taken", "sandbox"))
        fake_sandbox_client["get_results"].append({"name": name, "id": "new", "status": "stopped"})
    config = {"configurable": {"thread_id": "t-1"}}
    recovered = runtime._recover_managed_sandbox(sandbox, config, backend)
    assert recovered is not backend
    assert fake_sandbox_client["create_calls"][-1] == {"name": name, "snapshot_name": RECIPE}
    assert _resolve(sandbox) is recovered
    assert runtime._recover_managed_sandbox(sandbox, config, backend) is recovered
    assert len(fake_sandbox_client["get_calls"]) == (2 if conflict else 1)
    assert fake_sandbox_client["start_calls"] == []
    assert fake_sandbox_client["delete_calls"] == []


def test_recovery_keeps_cache_after_lookup_failure(fake_sandbox_client: dict[str, Any]) -> None:
    sandbox = _langsmith()
    backend = _resolve(sandbox)
    failure = RuntimeError("HTTP 503")
    fake_sandbox_client["get_errors"].append(failure)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        runtime._recover_managed_sandbox(sandbox, {"configurable": {"thread_id": "t-1"}}, backend)
    assert _resolve(sandbox) is backend
    assert len(fake_sandbox_client["create_calls"]) == 1


def test_recovery_does_not_use_new_name_after_unresolved_conflict(
    fake_sandbox_client: dict[str, Any],
) -> None:
    sandbox = _langsmith()
    backend = _resolve(sandbox)
    fake_sandbox_client["get_errors"].extend(
        [ResourceNotFoundError("missing", "sandbox"), ResourceNotFoundError("missing", "sandbox")]
    )
    fake_sandbox_client["errors"].append(ResourceAlreadyExistsError("taken", "sandbox"))
    with pytest.raises(ResourceNotFoundError):
        runtime._recover_managed_sandbox(sandbox, {"configurable": {"thread_id": "t-1"}}, backend)
    assert len(fake_sandbox_client["create_calls"]) == 2
    assert _resolve(sandbox) is backend


def _helper_config(lead: str = "t-lead") -> dict[str, Any]:
    """Run config of an async subagent that deepagents launched from thread ``lead``."""
    return {"configurable": {"thread_id": "t-helper", "deepagents_parent": {"thread_id": lead}}}


@pytest.fixture
def lead_verified() -> Iterator[None]:
    """``parent_run`` confirmed the helper's caller may access thread ``t-lead``."""
    token = runtime._VERIFIED_LEAD_THREAD.set("t-lead")
    yield
    runtime._VERIFIED_LEAD_THREAD.reset(token)


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("LANGSMITH_DEPLOYMENT_NAME", "studio")
    return "studio"


@pytest.mark.usefixtures("lead_verified")
def test_async_subagent_shares_its_leads_sandbox(fake_sandbox_client: dict[str, Any]) -> None:
    sandbox = _langsmith()

    lead = _resolve(sandbox, "t-lead")
    helper = runtime._resolve_managed_sandbox(sandbox, _helper_config())

    assert helper is lead
    assert len(fake_sandbox_client["create_calls"]) == 1


@pytest.mark.usefixtures("lead_verified")
def test_async_subagent_adopts_its_leads_sandbox_from_another_process(
    fake_sandbox_client: dict[str, Any], deployed: str
) -> None:
    name = runtime._managed_sandbox_name(deployed, "thread:t-lead")
    fake_sandbox_client["get_by_name"][name] = {"name": name}

    helper = runtime._resolve_managed_sandbox(_langsmith(), _helper_config())

    assert helper.id == name
    assert fake_sandbox_client["create_calls"] == []


@pytest.mark.usefixtures("lead_verified")
def test_async_subagent_only_refreshes_credentials_in_its_leads_sandbox(
    fake_sandbox_client: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox = _langsmith()
    _resolve(sandbox, "t-lead")
    modes: list[str] = []
    monkeypatch.setattr(runtime, "has_sandbox_connectors", lambda _: True)
    monkeypatch.setattr(
        runtime, "run_connector_sandbox_hooks", lambda *_, mode, **__: modes.append(mode)
    )

    runtime._resolve_managed_sandbox(sandbox, _helper_config())

    assert modes == ["credentials"]


@pytest.mark.usefixtures("lead_verified")
@pytest.mark.parametrize(("lead_exists", "mode"), [(True, "credentials"), (False, "create")])
def test_async_subagent_on_another_worker_never_reruns_setup_in_its_leads_sandbox(
    fake_sandbox_client: dict[str, Any],
    deployed: str,
    monkeypatch: pytest.MonkeyPatch,
    lead_exists: bool,
    mode: str,
) -> None:
    name = runtime._managed_sandbox_name(deployed, "thread:t-lead")
    if lead_exists:
        fake_sandbox_client["get_by_name"][name] = {"name": name}
    modes: list[str] = []
    monkeypatch.setattr(runtime, "has_sandbox_connectors", lambda _: True)
    monkeypatch.setattr(
        runtime, "run_connector_sandbox_hooks", lambda *_, mode, **__: modes.append(mode)
    )

    runtime._resolve_managed_sandbox(_langsmith(), _helper_config())

    created = [call.get("name") for call in fake_sandbox_client["create_calls"]]
    assert created == ([] if lead_exists else [name])
    assert modes == [mode]


def test_unverified_caller_keeps_its_own_sandbox(fake_sandbox_client: dict[str, Any]) -> None:
    sandbox = _langsmith()

    lead = _resolve(sandbox, "t-lead")
    helper = runtime._resolve_managed_sandbox(sandbox, _helper_config())

    assert helper is not lead
    assert len(fake_sandbox_client["create_calls"]) == 2


@pytest.mark.usefixtures("lead_verified")
def test_async_subagent_of_another_lead_keeps_its_own_sandbox(
    fake_sandbox_client: dict[str, Any],
) -> None:
    sandbox = _langsmith()

    lead = _resolve(sandbox, "t-lead")
    helper = runtime._resolve_managed_sandbox(sandbox, _helper_config("t-other"))

    assert helper is not lead
    assert len(fake_sandbox_client["create_calls"]) == 2


# parent_run: verify the lead thread once per run, then nest the trace.


def _client(
    monkeypatch: pytest.MonkeyPatch, *, allowed: bool, owner: str | None = None
) -> MagicMock:
    thread: dict[str, Any] = {"thread_id": "t-lead", "metadata": {}}
    if owner is not None:
        thread["metadata"]["owner"] = owner
    client = MagicMock()
    client.threads.get = AsyncMock(
        return_value=thread if allowed else None,
        side_effect=None if allowed else RuntimeError("404: thread not found"),
    )
    monkeypatch.setattr(runtime, "get_client", lambda: client)
    return client


def _verified_lead(config: dict[str, Any]) -> str | None:
    async def run() -> str | None:
        async with runtime.parent_run(config):
            return runtime._VERIFIED_LEAD_THREAD.get()

    return asyncio.run(run())


def test_verifies_a_lead_thread_the_caller_can_access(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, allowed=True)

    assert _verified_lead(_helper_config()) == "t-lead"
    client.threads.get.assert_awaited_once_with("t-lead")


@pytest.mark.parametrize(
    ("caller", "owns"),
    [("owner@example.com", True), ("studio-reader", False)],
    ids=["owner", "reader-but-not-owner"],
)
def test_verifies_only_a_lead_thread_the_caller_owns(
    monkeypatch: pytest.MonkeyPatch, caller: str, owns: bool
) -> None:
    _client(monkeypatch, allowed=True, owner="owner@example.com")
    config = _helper_config()
    config["configurable"]["langgraph_auth_user"] = {"mda_user_id": caller}

    assert _verified_lead(config) == ("t-lead" if owns else None)


def test_does_not_verify_a_lead_thread_the_caller_cannot_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client(monkeypatch, allowed=False)

    assert _verified_lead(_helper_config()) is None


@pytest.mark.parametrize("config", [{"configurable": {"thread_id": "t-1"}}], ids=["no-parent"])
def test_skips_the_check_without_a_lead_thread(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]
) -> None:
    client = _client(monkeypatch, allowed=True)

    assert _verified_lead(config) is None
    client.threads.get.assert_not_called()


def test_resume_finds_its_lead_from_run_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    # A resume after an approval comes through channel ingress, without deepagents'
    # reference; the Agent Server copies the task thread's metadata into the run.
    client = _client(monkeypatch, allowed=True)
    config = {"configurable": {"thread_id": "t-helper"}, "metadata": {"parent_thread_id": "t-lead"}}

    assert _verified_lead(config) == "t-lead"
    client.threads.get.assert_awaited_once_with("t-lead")


PARENT_TRACE = "20260101T000000000000Z01a111d6-47b6-7562-b56e-a4ebf257985d"


def _launched_config(**configurable: Any) -> dict[str, Any]:
    """The run config the Agent Server builds from deepagents' trace headers."""
    base: dict[str, Any] = {
        "langsmith-trace": PARENT_TRACE,
        "langsmith-metadata": {"ls_nest_under_parent": True},
        "langsmith-project": "parent project",
    }
    return {"configurable": {**base, **configurable}}


def test_run_nests_under_the_launching_call_when_tracing() -> None:
    with tracing_context(enabled=True), runtime._parent_trace(_launched_config()):
        parent = get_tracing_context()["parent"]

    assert (parent.dotted_order, parent.session_name) == (PARENT_TRACE, "parent project")


def test_parent_trace_does_not_turn_tracing_on() -> None:
    with tracing_context(enabled=False), runtime._parent_trace(_launched_config()):
        assert get_tracing_context()["parent"] is None


@pytest.mark.parametrize(
    "configurable",
    [
        {"langsmith-metadata": {}},
        {"langsmith-metadata": {"ls_nest_under_parent": "true"}},
        {"langsmith-metadata": None},
        {"langsmith-trace": "nope"},
    ],
    ids=["unmarked", "non-boolean marker", "no metadata", "malformed trace"],
)
def test_run_keeps_its_own_trace_without_a_valid_marked_parent(
    configurable: dict[str, Any],
) -> None:
    with tracing_context(enabled=True), runtime._parent_trace(_launched_config(**configurable)):
        assert get_tracing_context()["parent"] is None


def test_callers_metadata_and_tags_are_not_applied() -> None:
    metadata = {"ls_nest_under_parent": True, "thread_id": "t-parent"}
    config = _launched_config(**{"langsmith-metadata": metadata, "langsmith-tags": ["a"]})

    with tracing_context(enabled=True), runtime._parent_trace(config):
        context = get_tracing_context()

    assert context["parent"].metadata == {}
    assert not context["tags"]


def test_parent_run_nests_the_trace() -> None:
    async def parent_inside() -> object:
        async with runtime.parent_run(_launched_config()):
            return get_tracing_context()["parent"]

    with tracing_context(enabled=True):
        parent = asyncio.run(parent_inside())

    assert parent.dotted_order == PARENT_TRACE
