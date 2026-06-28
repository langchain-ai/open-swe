"""Non-blocking sandbox command auditing.

Every shell command executed inside a sandbox is classified by risk level using
static regex rules and asynchronously reported to LangSmith.  Classification
and reporting never delay command execution.

Risk levels:
- HIGH  — Command matches a destructive pattern (e.g. ``rm -rf /``, ``mkfs``).
          Blocked outright; the underlying sandbox never receives it.
- MEDIUM — Command matches a potentially dangerous pattern (e.g. ``git push --force``).
           Logged at WARNING level and allowed to proceed.
- LOW   — Normal developer command.  Allowed silently.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static classification rules
# ---------------------------------------------------------------------------

# Commands that can irrecoverably destroy the host OS or expose root filesystem.
_HIGH_RISK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-[rf]*\s*/"),                  # rm -rf /
    re.compile(r"\brm\s+-[rf]*\s+--no-preserve-root"), # rm -rf --no-preserve-root
    re.compile(r"\bmkfs\b"),                            # filesystem formatting
    re.compile(r"\bdd\s+.*of=/dev/"),                  # raw disk overwrite
    re.compile(r"\bchmod\s+-R\s+777\s*/"),             # world-writable root
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bpoweroff\b"),
]

# Commands that are risky but not catastrophic — log a warning and proceed.
_MEDIUM_RISK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-[rf]+"),           # generic rm -rf (non-root)
    re.compile(r"\bgit\s+push\b.*--force"), # force-push
]


def classify_command(command: str) -> tuple[str, str]:
    """Return ``(risk_level, reason)`` for *command* using static rules only.

    This function is intentionally free of network calls or LLM invocations so
    that it adds negligible latency to every sandbox ``execute()`` call.
    """
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(command):
            return "HIGH", "Matched critical blocked command pattern."
    for pattern in _MEDIUM_RISK_PATTERNS:
        if pattern.search(command):
            return "MEDIUM", "Matched potentially destructive command pattern."
    return "LOW", "No dangerous patterns matched."


# ---------------------------------------------------------------------------
# LangSmith audit (fire-and-forget)
# ---------------------------------------------------------------------------

def _emit_langsmith_audit(
    thread_id: str | None,
    command: str,
    risk_level: str,
    reason: str,
    duration: float,
    exit_code: int | None,
) -> None:
    """Send one structured audit record to LangSmith.

    Runs inside a daemon thread started by :class:`AuditedSandboxWrapper`.
    Any exception is silently swallowed so that audit failures are never
    surfaced to the agent.
    """
    try:
        import os
        import uuid

        from langsmith import Client as LangSmithClient

        api_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")
        if not api_key:
            return

        run_id = str(uuid.uuid4())
        client = LangSmithClient(api_key=api_key)
        client.create_run(
            id=run_id,
            name="sandbox_command",
            run_type="tool",
            inputs={"command": command},
            tags=[f"risk:{risk_level.lower()}", "sandbox-audit"],
            extra={
                "metadata": {
                    "thread_id": thread_id,
                    "risk_level": risk_level,
                    "reason": reason,
                }
            },
        )
        client.update_run(
            run_id=run_id,
            outputs={
                "exit_code": exit_code,
                "duration_seconds": round(duration, 3),
                "blocked": risk_level == "HIGH",
            },
        )
    except Exception:
        pass  # audit must never affect the main execution path


def _audit_async(
    thread_id: str | None,
    command: str,
    risk_level: str,
    reason: str,
    duration: float,
    exit_code: int | None,
) -> None:
    """Dispatch :func:`_emit_langsmith_audit` in a daemon thread."""
    threading.Thread(
        target=_emit_langsmith_audit,
        args=(thread_id, command, risk_level, reason, duration, exit_code),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------

class AuditedSandboxWrapper:
    """Non-blocking proxy that classifies and audits every sandbox ``execute()`` call.

    Wrap a raw sandbox backend with this class to gain:

    * **Static risk classification** — regex-based, zero latency overhead.
    * **HIGH-risk blocking** — truly destructive commands (e.g. ``rm -rf /``)
      are rejected before reaching the sandbox.
    * **Async LangSmith reporting** — all commands are asynchronously emitted
      as ``sandbox_command`` tool runs in LangSmith when
      ``LANGSMITH_API_KEY`` is set.  The background thread is a daemon and
      will not block process shutdown.

    All other sandbox methods (file I/O, glob, grep …) are transparently
    forwarded to the underlying backend via ``__getattr__``.
    """

    def __init__(self, raw_sandbox: SandboxBackendProtocol, thread_id: str | None = None):
        self._raw_sandbox = raw_sandbox
        self._thread_id = thread_id

    @property
    def id(self) -> str:
        return self._raw_sandbox.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        start = time.monotonic()
        risk_level, reason = classify_command(command)

        if risk_level == "HIGH":
            duration = time.monotonic() - start
            logger.warning(
                "sandbox:blocked thread=%s risk=HIGH command=%r reason=%s",
                self._thread_id,
                command,
                reason,
            )
            _audit_async(self._thread_id, command, risk_level, reason, duration, exit_code=1)
            return ExecuteResponse(
                output=f"[BLOCKED] Command rejected by sandbox safety rules: {reason}",
                exit_code=1,
                truncated=False,
            )

        if risk_level == "MEDIUM":
            logger.warning(
                "sandbox:medium-risk thread=%s command=%r reason=%s",
                self._thread_id,
                command,
                reason,
            )

        try:
            res = self._raw_sandbox.execute(command, timeout=timeout)
        except Exception:
            duration = time.monotonic() - start
            _audit_async(self._thread_id, command, risk_level, reason, duration, exit_code=None)
            raise

        duration = time.monotonic() - start
        _audit_async(self._thread_id, command, risk_level, reason, duration, res.exit_code)
        return res

    def __getattr__(self, name: str):
        return getattr(self._raw_sandbox, name)
