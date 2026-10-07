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
    _create_sandbox_with_retry,
    _install_create_extra_fields,
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
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def create_sandbox(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.calls <= self.failures:
            raise _RetryableCreateError("try again")
        return {"sandbox": kwargs["snapshot_id"]}


@pytest.mark.asyncio
async def test_create_sandbox_with_retry_retries_transient_errors(monkeypatch) -> None:  # noqa: ANN001
    client = _FakeSandboxClient(failures=2)
    monkeypatch.setattr("openswe.sandboxes.providers.langsmith.asyncio.sleep", AsyncMock())

    result = await _create_sandbox_with_retry(
        cast(AsyncSandboxClient, client),
        snapshot_id="snap-1",
        fs_capacity_bytes=None,
        vcpus=None,
        mem_bytes=None,
        idle_ttl_seconds=None,
        delete_after_stop_seconds=None,
        timeout=180,
    )

    assert result == {"sandbox": "snap-1"}
    assert client.calls == 3
    assert "name" not in client.last_kwargs


@pytest.mark.asyncio
async def test_install_create_extra_fields_merges_only_boxes_post() -> None:
    calls: list[tuple[str, dict]] = []

    class _FakeHttp:
        async def post(self, url, **kwargs):  # noqa: ANN001, ANN003
            payload = kwargs.get("json")
            assert isinstance(payload, dict)
            calls.append((url, payload))
            return "ok"

    class _FakeClient:
        def __init__(self) -> None:
            self._http = _FakeHttp()

    client = _FakeClient()
    _install_create_extra_fields(cast(AsyncSandboxClient, client), {"_internal_runtime": "v2"})

    await client._http.post("https://api/v2/sandboxes/boxes", json={"snapshot_id": "s"})
    await client._http.post("https://api/v2/sandboxes/boxes/abc/start", json={"foo": "bar"})

    assert calls[0][1] == {"snapshot_id": "s", "_internal_runtime": "v2"}
    assert calls[1][1] == {"foo": "bar"}


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
