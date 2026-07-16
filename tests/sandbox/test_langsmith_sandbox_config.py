"""Tests for LangSmith sandbox env-var configuration parsing."""

import base64
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from agent.integrations.langsmith import (
    DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS,
    DEFAULT_SANDBOX_IDLE_TTL_SECONDS,
    DEFAULT_SANDBOX_MEM_BYTES,
    DEFAULT_SANDBOX_VCPUS,
    DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES,
    LangSmithProvider,
    _create_sandbox_with_retry,
    _get_sandbox_api_endpoint,
    _get_sandbox_snapshot_config,
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
        client,
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
        client,
        "sandbox-1",
        timeout_seconds=30,
        poll_seconds=2,
    )

    assert sandbox.status == "running"
    assert client.calls == 3
    assert sleep.await_count == 2
