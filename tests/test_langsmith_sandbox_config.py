"""Tests for LangSmith sandbox env-var configuration parsing."""

from unittest.mock import patch

import pytest

from agent.integrations.langsmith import (
    DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS,
    DEFAULT_SANDBOX_IDLE_TTL_SECONDS,
    DEFAULT_SANDBOX_MEM_BYTES,
    DEFAULT_SANDBOX_VCPUS,
    DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES,
    LangSmithProvider,
    _get_sandbox_snapshot_config,
)


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
