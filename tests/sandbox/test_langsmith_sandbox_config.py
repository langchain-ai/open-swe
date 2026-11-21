"""Tests for LangSmith sandbox env-var configuration parsing."""

import json
from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from langsmith.sandbox import AsyncSandboxClient, ResourceNotFoundError

from openswe.sandboxes.providers import langsmith as langsmith_provider
from openswe.sandboxes.providers.langsmith import (
    LangSmithCreateOptions,
    LangSmithProvider,
    _create_sandbox_with_retry,
    _reuse_existing_sandbox,
    capture_snapshot_with_tag,
    create_langsmith_sandbox,
    create_workspace_service_url,
)
from openswe.sandboxes.providers.registry import SandboxGoneError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected_vcpus", "expected_mem_bytes"),
    [
        ({"vcpus": 8}, 8, None),
        ({"mem_bytes": 8_000}, None, 8_000),
    ],
)
async def test_create_langsmith_sandbox_derives_partial_cpu_memory_overrides(
    overrides: dict[str, int],
    expected_vcpus: int | None,
    expected_mem_bytes: int | None,
) -> None:
    provider = MagicMock()
    provider.get_or_create = AsyncMock(return_value=AsyncMock())
    with (
        patch(
            "openswe.sandboxes.providers.langsmith._get_sandbox_snapshot_config",
            return_value=(100, 2, 200, 300, 400),
        ),
        patch("openswe.sandboxes.providers.langsmith.LangSmithProvider", return_value=provider),
    ):
        await create_langsmith_sandbox(
            mem_bytes=overrides.get("mem_bytes"),
            vcpus=overrides.get("vcpus"),
        )

    assert provider.get_or_create.await_args is not None
    assert provider.get_or_create.await_args.kwargs["vcpus"] == expected_vcpus
    assert provider.get_or_create.await_args.kwargs["mem_bytes"] == expected_mem_bytes


class _RetryableCreateError(Exception):
    status_code = 503


class _FakeSandboxClient:
    def __init__(self, failures: int, error: Callable[[], Exception]) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    async def create_sandbox(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error()
        return {"sandbox": kwargs["snapshot_id"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [lambda: _RetryableCreateError("try again"), lambda: httpx2.ReadTimeout("")],
    ids=["retryable-status", "read-timeout"],
)
async def test_create_sandbox_with_retry_retries_transient_errors(
    monkeypatch: pytest.MonkeyPatch, error: Callable[[], Exception]
) -> None:
    client = _FakeSandboxClient(failures=2, error=error)
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.asyncio.sleep", AsyncMock())

    result = await _create_sandbox_with_retry(
        lambda: client.create_sandbox(snapshot_id="snap-1", timeout=180)
    )

    assert result == {"sandbox": "snap-1"}
    assert client.calls == 3


@pytest.mark.asyncio
async def test_create_params_go_to_create_sandbox_as_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []

    class _Client:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def create_sandbox(self, **kwargs: object) -> MagicMock:
            created.append(kwargs)
            return MagicMock()

    monkeypatch.setattr(langsmith_provider, "AsyncSandboxClient", _Client)
    monkeypatch.setattr(langsmith_provider, "TimeoutLangSmithSandbox", MagicMock())
    provider = LangSmithProvider(api_key="test-key")
    tag = "00000000-0000-0000-0000-000000000001"
    await provider.get_or_create(
        snapshot_id=None,
        idle_ttl_seconds=300,
        delete_after_stop_seconds=400,
        create_params={
            "preserve_memory_on_stop": True,
            "tag_value_ids": [tag],
            "delete_after_stop_seconds": 60,
            "mount_config": {"mounts": [], "auth": {"aws": None, "gcp": {"service_account": "x"}}},
        },
    )
    assert created == [
        {
            "timeout": 180,
            "snapshot_id": "",
            "fs_capacity_bytes": None,
            "vcpus": None,
            "mem_bytes": None,
            "idle_ttl_seconds": 300,
            "delete_after_stop_seconds": 60,
            "preserve_memory_on_stop": True,
            "tag_value_ids": [tag],
            "mount_config": {"mounts": [], "auth": {"gcp": {"service_account": "x"}}},
        }
    ]
    with pytest.raises(ValueError, match="create_params cannot set _internal_runtime"):
        await provider.get_or_create(create_params={"_internal_runtime": "v2"})
    assert len(created) == 1


def test_mount_config_argument_drops_only_nulls() -> None:
    from runtime_api.catalog import SandboxCreate

    assert SandboxCreate.mount_config_argument({"mounts": [], "auth": None}) == {"mounts": []}
    assert SandboxCreate.mount_config_argument({"auth": {"aws": None, "gcp": {"x": 1}}}) == {
        "auth": {"gcp": {"x": 1}}
    }
    # Left for the sandbox API to reject, which it does when no mounts are listed.
    for kept in ({"auth": {}}, {"auth": False}):
        assert SandboxCreate.mount_config_argument(kept) == kept


def test_create_options_take_create_params_ttls_and_reject_other_keys() -> None:
    with patch(
        "openswe.sandboxes.providers.langsmith._get_sandbox_snapshot_config",
        return_value=(100, 2, 200, 300, 400),
    ):
        options = LangSmithCreateOptions.resolve(
            create_params={"idle_ttl_seconds": 600, "preserve_memory_on_stop": True}
        )
        assert (options.idle_ttl_seconds, options.delete_after_stop_seconds) == (600, 400)
        assert options.extra_fields == {"preserve_memory_on_stop": True}
        # A null TTL keeps the deployment's default.
        options = LangSmithCreateOptions.resolve(
            create_params={"idle_ttl_seconds": None, "delete_after_stop_seconds": None}
        )
        assert (options.idle_ttl_seconds, options.delete_after_stop_seconds) == (300, 400)
        assert options.extra_fields == {}
        with pytest.raises(ValueError, match="create_params cannot set region"):
            LangSmithCreateOptions.resolve(create_params={"region": "us"})


@pytest.mark.asyncio
async def test_capture_snapshot_request_outlasts_the_client_default_timeout() -> None:
    sent: list[dict[str, object]] = []

    class _FakeHttp:
        async def post(self, url: str, **kwargs: object) -> None:
            sent.append(kwargs)

    class _FakeClient:
        def __init__(self) -> None:
            self._http = _FakeHttp()

        async def capture_snapshot(self, sandbox_id: str, name: str, *, timeout: int) -> None:
            await self._http.post("https://api/v2/sandboxes/boxes/x/snapshot", json={"name": name})

    await capture_snapshot_with_tag(
        cast(AsyncSandboxClient, _FakeClient()), "sb-1", "acme", "latest", timeout=60
    )

    assert sent == [
        {
            "json": {"name": "acme", "tag": "latest"},
            "timeout": langsmith_provider.SNAPSHOT_CAPTURE_REQUEST_TIMEOUT_SECONDS,
        }
    ]


@pytest.mark.asyncio
async def test_capture_snapshot_restores_the_client_after_a_failure() -> None:
    class _FakeHttp:
        async def post(self, url, **kwargs):  # noqa: ANN001, ANN003
            raise RuntimeError("capture exploded")

    class _FakeClient:
        def __init__(self) -> None:
            self._http = _FakeHttp()
            self.original_post = self._http.post

        async def capture_snapshot(self, sandbox_id: str, name: str, *, timeout: int) -> str:
            return await self._http.post("https://api/v2/sandboxes/boxes/x/snapshot", json={})

    client = _FakeClient()
    with pytest.raises(RuntimeError, match="capture exploded"):
        await capture_snapshot_with_tag(
            cast(AsyncSandboxClient, client), "sb-1", "acme-monorepo", "latest", timeout=60
        )

    assert client._http.post == client.original_post


class _MissingSandboxClient:
    """get_sandbox raises instead of returning a sandbox."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def get_sandbox(self, *, name: str) -> None:
        raise self.exc


@pytest.mark.asyncio
async def test_reuse_reports_a_deleted_sandbox_as_gone() -> None:
    client = _MissingSandboxClient(ResourceNotFoundError("Sandbox 'openswe-abc' not found"))
    with pytest.raises(SandboxGoneError):
        await _reuse_existing_sandbox(cast(AsyncSandboxClient, client), "openswe-abc")


@pytest.mark.asyncio
async def test_reuse_keeps_other_failures_untyped() -> None:
    client = _MissingSandboxClient(RuntimeError("boom"))
    with pytest.raises(RuntimeError) as excinfo:
        await _reuse_existing_sandbox(cast(AsyncSandboxClient, client), "openswe-abc")
    assert not isinstance(excinfo.value, SandboxGoneError)


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    """Answer the provider's own client from `handler` instead of the network."""
    client = httpx2.AsyncClient
    monkeypatch.setattr(
        langsmith_provider.httpx2,
        "AsyncClient",
        lambda **_kwargs: client(transport=httpx2.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_service_url_asks_for_a_workspace_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                # LangSmith answers with browser_url too; in workspace mode it is the same URL.
                "browser_url": "https://l-abc.sandbox.example/",
                "service_url": "https://l-abc.sandbox.example/",
                "access": "workspace",
            },
        )

    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-key")
    _serve(monkeypatch, handler)

    service_url = await create_workspace_service_url("sandbox-1", 3000)

    assert service_url == "https://l-abc.sandbox.example/"
    request = requests[0]
    assert str(request.url) == (
        "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-1/service-url"
    )
    assert request.headers["X-API-Key"] == "lsv2-key"
    assert json.loads(request.content) == {"port": 3000, "access": "workspace"}
