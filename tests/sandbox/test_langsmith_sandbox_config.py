"""Tests for LangSmith sandbox env-var configuration parsing."""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langsmith.sandbox import AsyncSandboxClient, ResourceNotFoundError

from agent.sandboxes.providers.langsmith import (
    DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS,
    DEFAULT_SANDBOX_IDLE_TTL_SECONDS,
    DEFAULT_SANDBOX_MEM_BYTES,
    DEFAULT_SANDBOX_VCPUS,
    DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES,
    LangSmithProvider,
    _create_sandbox_with_retry,
    _get_sandbox_api_endpoint,
    _get_sandbox_create_extra_fields,
    _get_sandbox_snapshot_config,
    _install_create_extra_fields,
    _merge_sandbox_create_extra_fields,
    _reuse_existing_sandbox,
    create_langsmith_sandbox,
    create_langsmith_sandbox_from_params,
)
from agent.sandboxes.providers.registry import SandboxGoneError


def test_sandbox_api_endpoint_appends_v2_sandboxes() -> None:
    with patch.dict("os.environ", {"LANGSMITH_ENDPOINT": "https://eu.smith.langchain.com"}):
        assert _get_sandbox_api_endpoint() == "https://eu.smith.langchain.com/v2/sandboxes"


def test_sandbox_api_endpoint_no_double_suffix() -> None:
    with patch.dict(
        "os.environ",
        {"SANDBOX_LANGSMITH_ENDPOINT": "https://x.smith.langchain.com/v2/sandboxes"},
    ):
        assert _get_sandbox_api_endpoint() == "https://x.smith.langchain.com/v2/sandboxes"


def test_nothing_deletes_sandboxes() -> None:
    """No code path may delete a sandbox.

    A sandbox holds the agent's only copy of its working tree, and the metadata
    read (``get_sandbox_id_from_metadata``) fails open to "this thread has no
    sandbox". A delete keyed off that guess destroys a running box. Reclamation
    belongs to the platform's idle TTL and delete-after-stop.
    """
    agent_root = Path(__file__).resolve().parents[2] / "agent"
    offenders = [
        f"{path.relative_to(agent_root)}:{lineno}"
        for path in agent_root.rglob("*.py")
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if "delete_sandbox" in line
    ]
    assert offenders == []


def test_defaults_when_env_unset() -> None:
    with patch.dict(
        "os.environ",
        {"DEFAULT_SANDBOX_SNAPSHOT_ID": "snap-1"},
        clear=True,
    ):
        snapshot_id, fs, vcpus, mem, idle, delete_after = _get_sandbox_snapshot_config()
    assert snapshot_id == "snap-1"
    assert fs == DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES
    assert vcpus == DEFAULT_SANDBOX_VCPUS
    assert mem == DEFAULT_SANDBOX_MEM_BYTES
    assert idle == DEFAULT_SANDBOX_IDLE_TTL_SECONDS
    assert DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS == 30 * 24 * 60 * 60
    assert delete_after == DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS


def test_overrides_from_env() -> None:
    with patch.dict(
        "os.environ",
        {
            "DEFAULT_SANDBOX_SNAPSHOT_ID": "snap-2",
            "DEFAULT_SANDBOX_IDLE_TTL_SECONDS": "120",
            "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS": "3600",
        },
        clear=True,
    ):
        _, _, _, _, idle, delete_after = _get_sandbox_snapshot_config()
    assert idle == 120
    assert delete_after == 3600


@pytest.mark.asyncio
async def test_create_langsmith_sandbox_prefers_resource_overrides() -> None:
    provider = MagicMock()
    provider.get_or_create = AsyncMock(return_value=MagicMock())
    with (
        patch(
            "agent.sandboxes.providers.langsmith._get_sandbox_snapshot_config",
            return_value=("default-snap", 100, 2, 200, 300, 400),
        ),
        patch("agent.sandboxes.providers.langsmith.LangSmithProvider", return_value=provider),
    ):
        await create_langsmith_sandbox(
            snapshot_id="env-snap",
            mem_bytes=2_000,
            vcpus=8,
            fs_capacity_bytes=1_000,
            create_params={"_internal_runtime": "v2"},
        )

    provider.get_or_create.assert_awaited_once_with(
        sandbox_id=None,
        snapshot_id="env-snap",
        fs_capacity_bytes=1_000,
        vcpus=8,
        mem_bytes=2_000,
        idle_ttl_seconds=300,
        delete_after_stop_seconds=400,
        create_params={"_internal_runtime": "v2"},
    )


@pytest.mark.asyncio
async def test_create_langsmith_sandbox_uses_root_snapshot_when_unset() -> None:
    provider = MagicMock()
    provider.get_or_create = AsyncMock(return_value=MagicMock())
    with (
        patch(
            "agent.sandboxes.providers.langsmith._get_sandbox_snapshot_config",
            return_value=(None, 100, 2, 200, 300, 400),
        ),
        patch("agent.sandboxes.providers.langsmith.LangSmithProvider", return_value=provider),
    ):
        await create_langsmith_sandbox()

    assert provider.get_or_create.await_args is not None
    assert provider.get_or_create.await_args.kwargs["snapshot_id"] == ""


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
    provider.get_or_create = AsyncMock(return_value=MagicMock())
    with (
        patch(
            "agent.sandboxes.providers.langsmith._get_sandbox_snapshot_config",
            return_value=("default-snap", 100, 2, 200, 300, 400),
        ),
        patch("agent.sandboxes.providers.langsmith.LangSmithProvider", return_value=provider),
    ):
        await create_langsmith_sandbox(
            mem_bytes=overrides.get("mem_bytes"),
            vcpus=overrides.get("vcpus"),
        )

    assert provider.get_or_create.await_args is not None
    assert provider.get_or_create.await_args.kwargs["vcpus"] == expected_vcpus
    assert provider.get_or_create.await_args.kwargs["mem_bytes"] == expected_mem_bytes


def test_zero_disables_ttls() -> None:
    with patch.dict(
        "os.environ",
        {
            "DEFAULT_SANDBOX_SNAPSHOT_ID": "snap-3",
            "DEFAULT_SANDBOX_IDLE_TTL_SECONDS": "0",
            "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS": "0",
        },
        clear=True,
    ):
        _, _, _, _, idle, delete_after = _get_sandbox_snapshot_config()
    assert idle == 0
    assert delete_after == 0


def test_validate_startup_rejects_non_integer_ttl() -> None:
    with patch.dict(
        "os.environ",
        {
            "DEFAULT_SANDBOX_SNAPSHOT_ID": "snap-4",
            "DEFAULT_SANDBOX_IDLE_TTL_SECONDS": "not-a-number",
        },
        clear=True,
    ):
        with pytest.raises(ValueError, match="DEFAULT_SANDBOX_IDLE_TTL_SECONDS"):
            LangSmithProvider.validate_startup_config()


def test_validate_startup_rejects_negative_ttl() -> None:
    with patch.dict(
        "os.environ",
        {
            "DEFAULT_SANDBOX_SNAPSHOT_ID": "snap-5",
            "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS": "-1",
        },
        clear=True,
    ):
        with pytest.raises(ValueError, match=">= 0"):
            LangSmithProvider.validate_startup_config()


def test_validate_startup_accepts_valid_config() -> None:
    with patch.dict(
        "os.environ",
        {
            "DEFAULT_SANDBOX_SNAPSHOT_ID": "snap-6",
            "DEFAULT_SANDBOX_IDLE_TTL_SECONDS": "1800",
            "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS": "86400",
        },
        clear=True,
    ):
        LangSmithProvider.validate_startup_config()


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
@pytest.mark.parametrize(
    ("snapshot_id", "create_params"),
    [(None, None), ("", None), (None, {"snapshot_id": ""})],
)
async def test_provider_omits_snapshot_id_when_unset(
    snapshot_id: str | None, create_params: dict[str, str] | None
) -> None:
    """No usable snapshot must send no `snapshot_id` key at all.

    The API boots its default root snapshot only when the field is absent. It
    is a UUID server-side, so sending "" is rejected with a 422 before any
    validation runs, and no sandbox is created.
    """
    client = AsyncSandboxClient(api_key="key", api_endpoint="https://example.com/v2/sandboxes")
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"name": "sandbox-new", "status": "ready"}
    post = AsyncMock(return_value=response)
    client._http.post = post

    with patch("agent.sandboxes.providers.langsmith.AsyncSandboxClient", return_value=client):
        await LangSmithProvider(api_key="key").get_or_create(
            snapshot_id=snapshot_id, create_params=create_params
        )

    assert post.await_args is not None
    assert "snapshot_id" not in post.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_create_sandbox_with_retry_retries_transient_errors(monkeypatch) -> None:  # noqa: ANN001
    client = _FakeSandboxClient(failures=2)
    monkeypatch.setattr("agent.sandboxes.providers.langsmith.asyncio.sleep", AsyncMock())

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
async def test_create_from_params_forwards_public_and_hidden_options() -> None:
    sandbox = MagicMock(name="sandbox-new")
    sandbox.name = "sandbox-new"
    sandbox.to_sync.return_value = MagicMock(id="sandbox-new")
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_sandbox = AsyncMock(return_value=sandbox)
    client.wait_for_sandbox = AsyncMock(return_value=sandbox)

    with (
        patch("agent.sandboxes.providers.langsmith.AsyncSandboxClient", return_value=client),
        patch("agent.sandboxes.providers.langsmith._install_create_extra_fields") as install,
        patch(
            "agent.sandboxes.providers.langsmith._get_sandbox_create_extra_fields",
            return_value={},
        ),
    ):
        await create_langsmith_sandbox_from_params(
            {
                "snapshot_name": "python:latest",
                "wait_for_ready": False,
                "timeout": 240,
                "cpu_millicores": 500,
                "_internal_runtime": "v2",
            }
        )

    client.create_sandbox.assert_awaited_once_with(
        snapshot_name="python:latest",
        wait_for_ready=False,
        timeout=240,
    )
    client.wait_for_sandbox.assert_awaited_once_with("sandbox-new", timeout=240)
    install.assert_called_once_with(
        client,
        {"cpu_millicores": 500, "_internal_runtime": "v2"},
    )


def test_extra_fields_unset_is_empty() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert _get_sandbox_create_extra_fields() == {}
    with patch.dict("os.environ", {"SANDBOX_CREATE_EXTRA_JSON": "  "}, clear=True):
        assert _get_sandbox_create_extra_fields() == {}


def test_extra_fields_parsed() -> None:
    with patch.dict(
        "os.environ",
        {"SANDBOX_CREATE_EXTRA_JSON": '{"_internal_runtime": "v2"}'},
        clear=True,
    ):
        assert _get_sandbox_create_extra_fields() == {"_internal_runtime": "v2"}


def test_environment_create_params_override_deployment_defaults() -> None:
    with patch.dict(
        "os.environ",
        {"SANDBOX_CREATE_EXTRA_JSON": '{"_internal_runtime": "v1", "shared": true}'},
        clear=True,
    ):
        assert _merge_sandbox_create_extra_fields(
            {"_internal_runtime": "v2", "proxy_config": {"rules": []}}
        ) == {
            "_internal_runtime": "v2",
            "shared": True,
            "proxy_config": {"rules": []},
        }


def test_extra_fields_rejects_invalid_json() -> None:
    with patch.dict("os.environ", {"SANDBOX_CREATE_EXTRA_JSON": "{not json"}, clear=True):
        with pytest.raises(ValueError, match="valid JSON"):
            _get_sandbox_create_extra_fields()


def test_extra_fields_rejects_non_object() -> None:
    with patch.dict("os.environ", {"SANDBOX_CREATE_EXTRA_JSON": "[1, 2]"}, clear=True):
        with pytest.raises(ValueError, match="JSON object"):
            _get_sandbox_create_extra_fields()


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
async def test_install_create_extra_fields_noop_when_empty() -> None:
    class _FakeHttp:
        def __init__(self) -> None:
            self.post = "sentinel"

    class _FakeClient:
        def __init__(self) -> None:
            self._http = _FakeHttp()

    client = _FakeClient()
    _install_create_extra_fields(cast(AsyncSandboxClient, client), {})
    assert client._http.post == "sentinel"


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
