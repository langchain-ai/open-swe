"""Tests for LangSmith sandbox env-var configuration parsing."""

import base64
import uuid
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langsmith.sandbox import AsyncSandboxClient

from agent.integrations.langsmith import (
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
    _sandbox_name_for_thread,
    _wait_for_reconnected_sandbox,
)


def test_sandbox_api_endpoint_appends_v2_sandboxes() -> None:
    with patch.dict("os.environ", {"LANGSMITH_ENDPOINT": "https://eu.smith.langchain.com"}):
        assert _get_sandbox_api_endpoint() == "https://eu.smith.langchain.com/v2/sandboxes"


def test_sandbox_api_endpoint_no_double_suffix() -> None:
    with patch.dict(
        "os.environ",
        {"SANDBOX_LANGSMITH_ENDPOINT": "https://x.smith.langchain.com/v2/sandboxes"},
    ):
        assert _get_sandbox_api_endpoint() == "https://x.smith.langchain.com/v2/sandboxes"


def test_sandbox_name_for_thread_encodes_uuid() -> None:
    thread_id = "12345678-1234-5678-1234-567812345678"
    name = _sandbox_name_for_thread(thread_id)
    assert name is not None
    prefix, _, encoded = name.partition("-")
    assert prefix == "openswe"
    assert encoded == encoded.lower()
    assert "=" not in encoded and "-" not in encoded
    # Round-trips back to the original UUID.
    padded = encoded.upper() + "=" * (-len(encoded) % 8)
    assert uuid.UUID(bytes=base64.b32decode(padded)) == uuid.UUID(thread_id)


def test_sandbox_name_for_thread_none_or_invalid() -> None:
    assert _sandbox_name_for_thread(None) is None
    assert _sandbox_name_for_thread("not-a-uuid") is None


def test_nothing_deletes_sandboxes() -> None:
    """No code path may delete a sandbox.

    A sandbox holds the agent's only copy of its working tree, and callers can't
    tell a free name from one held by a live box: both the metadata read
    (``get_sandbox_id_from_metadata``) and write (``_update_thread_sandbox_metadata``)
    fail open to "this thread has no sandbox". A delete keyed off that guess
    destroys a running box. Reclamation belongs to the platform's idle TTL and
    delete-after-stop.
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


class _FakeStatusSandbox:
    def __init__(self, status: str) -> None:
        self.status = status


class _FakeReconnectClient:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.calls = 0

    async def get_sandbox(self, *, name: str) -> _FakeStatusSandbox:
        self.calls += 1
        status = self.statuses[min(self.calls - 1, len(self.statuses) - 1)]
        return _FakeStatusSandbox(status)


@pytest.mark.asyncio
async def test_create_sandbox_with_retry_retries_transient_errors(monkeypatch) -> None:  # noqa: ANN001
    client = _FakeSandboxClient(failures=2)
    monkeypatch.setattr("agent.integrations.langsmith.asyncio.sleep", AsyncMock())

    result = await _create_sandbox_with_retry(
        cast(AsyncSandboxClient, client),
        snapshot_id="snap-1",
        name="openswe-abc",
        fs_capacity_bytes=None,
        vcpus=None,
        mem_bytes=None,
        idle_ttl_seconds=None,
        delete_after_stop_seconds=None,
        timeout=180,
    )

    assert result == {"sandbox": "snap-1"}
    assert client.calls == 3
    assert client.last_kwargs["name"] == "openswe-abc"


@pytest.mark.asyncio
async def test_wait_for_reconnected_sandbox_polls_until_ready(monkeypatch) -> None:  # noqa: ANN001
    client = _FakeReconnectClient(["starting", "starting", "running"])
    sleep = AsyncMock()
    monkeypatch.setattr("agent.integrations.langsmith.asyncio.sleep", sleep)

    sandbox = await _wait_for_reconnected_sandbox(
        cast(AsyncSandboxClient, client),
        "sandbox-1",
        timeout_seconds=30,
        poll_seconds=2,
    )

    assert sandbox.status == "running"
    assert client.calls == 3
    assert sleep.await_count == 2


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
