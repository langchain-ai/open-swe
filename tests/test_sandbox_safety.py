"""Tests for sandbox command auditing (agent/utils/sandbox_safety.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from agent.utils.sandbox_safety import AuditedSandboxWrapper, classify_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockSandbox(SandboxBackendProtocol):
    """Minimal sandbox stub that records executed commands."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    @property
    def id(self) -> str:
        return "mock-sandbox-001"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        self.executed.append(command)
        return ExecuteResponse(output=f"ok: {command}", exit_code=0, truncated=False)


# ---------------------------------------------------------------------------
# classify_command
# ---------------------------------------------------------------------------


class TestClassifyCommand:
    def test_high_risk_rm_rf_root(self):
        level, _ = classify_command("rm -rf /")
        assert level == "HIGH"

    def test_high_risk_no_preserve_root(self):
        level, _ = classify_command("rm -rf --no-preserve-root /")
        assert level == "HIGH"

    def test_high_risk_mkfs(self):
        level, _ = classify_command("mkfs.ext4 /dev/sda1")
        assert level == "HIGH"

    def test_high_risk_dd_disk(self):
        level, _ = classify_command("dd if=/dev/zero of=/dev/sda")
        assert level == "HIGH"

    def test_high_risk_shutdown(self):
        level, _ = classify_command("shutdown -h now")
        assert level == "HIGH"

    def test_medium_risk_rm_rf(self):
        level, _ = classify_command("rm -rf ./dist")
        assert level == "MEDIUM"

    def test_medium_risk_force_push(self):
        level, _ = classify_command("git push origin main --force")
        assert level == "MEDIUM"

    def test_low_risk_git_status(self):
        level, _ = classify_command("git status")
        assert level == "LOW"

    def test_low_risk_pytest(self):
        level, _ = classify_command("uv run pytest -vvv tests/")
        assert level == "LOW"

    def test_low_risk_ls(self):
        level, _ = classify_command("ls -la")
        assert level == "LOW"

    def test_low_risk_curl_in_legitimate_context(self):
        # curl is not in the medium patterns — only force-push and rm -rf
        level, _ = classify_command("curl https://example.com")
        assert level == "LOW"


# ---------------------------------------------------------------------------
# AuditedSandboxWrapper — execution routing
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_sandbox() -> _MockSandbox:
    return _MockSandbox()


@pytest.fixture()
def wrapper(mock_sandbox: _MockSandbox) -> AuditedSandboxWrapper:
    return AuditedSandboxWrapper(mock_sandbox, thread_id="test-thread-001")


@pytest.fixture(autouse=True)
def _suppress_audit_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent real LangSmith network calls during tests."""
    monkeypatch.setattr("agent.utils.sandbox_safety._audit_async", lambda *a, **k: None)


class TestAuditedSandboxWrapperExecution:
    def test_low_risk_command_executes(self, wrapper: AuditedSandboxWrapper, mock_sandbox: _MockSandbox):
        res = wrapper.execute("git status")
        assert res.exit_code == 0
        assert "git status" in mock_sandbox.executed

    def test_medium_risk_command_executes_with_warning(
        self, wrapper: AuditedSandboxWrapper, mock_sandbox: _MockSandbox, caplog
    ):
        import logging

        with caplog.at_level(logging.WARNING, logger="agent.utils.sandbox_safety"):
            res = wrapper.execute("rm -rf ./build")

        assert res.exit_code == 0
        assert "rm -rf ./build" in mock_sandbox.executed
        assert "medium-risk" in caplog.text

    def test_high_risk_command_is_blocked(
        self, wrapper: AuditedSandboxWrapper, mock_sandbox: _MockSandbox
    ):
        res = wrapper.execute("rm -rf /")
        assert res.exit_code == 1
        assert "[BLOCKED]" in res.output
        # Underlying sandbox must not have received the command
        assert "rm -rf /" not in mock_sandbox.executed

    def test_high_risk_mkfs_is_blocked(self, wrapper: AuditedSandboxWrapper, mock_sandbox: _MockSandbox):
        res = wrapper.execute("mkfs.ext4 /dev/sda")
        assert res.exit_code == 1
        assert not mock_sandbox.executed

    def test_sandbox_exception_propagates(
        self, wrapper: AuditedSandboxWrapper, mock_sandbox: _MockSandbox
    ):
        mock_sandbox.execute = MagicMock(side_effect=RuntimeError("sandbox error"))
        with pytest.raises(RuntimeError, match="sandbox error"):
            wrapper.execute("ls")

    def test_id_property_delegates(self, wrapper: AuditedSandboxWrapper):
        assert wrapper.id == "mock-sandbox-001"

    def test_getattr_delegates_to_raw_sandbox(
        self, wrapper: AuditedSandboxWrapper, mock_sandbox: _MockSandbox
    ):
        # Access an attribute not overridden by the wrapper
        assert wrapper._raw_sandbox is mock_sandbox


# ---------------------------------------------------------------------------
# AuditedSandboxWrapper — audit emission (unit)
# ---------------------------------------------------------------------------


class TestAuditEmission:
    def test_audit_fired_for_low_risk(self, wrapper: AuditedSandboxWrapper, monkeypatch: pytest.MonkeyPatch):
        calls: list[tuple] = []
        monkeypatch.setattr(
            "agent.utils.sandbox_safety._audit_async",
            lambda *a, **k: calls.append((a, k)),
        )
        wrapper.execute("git log --oneline")
        assert len(calls) == 1
        args = calls[0][0]
        assert args[2] == "LOW"  # risk_level

    def test_audit_fired_for_high_risk(self, wrapper: AuditedSandboxWrapper, monkeypatch: pytest.MonkeyPatch):
        calls: list[tuple] = []
        monkeypatch.setattr(
            "agent.utils.sandbox_safety._audit_async",
            lambda *a, **k: calls.append((a, k)),
        )
        wrapper.execute("shutdown now")
        assert len(calls) == 1
        args = calls[0][0]
        assert args[2] == "HIGH"

    def test_audit_not_raised_on_langsmith_failure(
        self, wrapper: AuditedSandboxWrapper, monkeypatch: pytest.MonkeyPatch
    ):
        """A LangSmith error inside the audit thread must not bubble up."""
        monkeypatch.setattr(
            "agent.utils.sandbox_safety._emit_langsmith_audit",
            MagicMock(side_effect=Exception("network error")),
        )
        # Restore real _audit_async so it calls _emit_langsmith_audit
        from agent.utils import sandbox_safety
        monkeypatch.setattr(sandbox_safety, "_audit_async", sandbox_safety._audit_async)

        # Should not raise
        res = wrapper.execute("git status")
        assert res.exit_code == 0
