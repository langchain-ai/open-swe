"""Sandbox git operations: timeouts and operator-facing hints (no secrets)."""

from __future__ import annotations

import logging
import os

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

logger = logging.getLogger(__name__)

_DEFAULT_GIT_TIMEOUT_SEC = 900
_MIN_GIT_TIMEOUT_SEC = 60

# Prefer IPv4 for HTTPS git in sandboxes: many environments return AAAA for dev.azure.com
# but have no working IPv6 egress; libcurl may try IPv6 first and hang or time out.
_GIT_CURL_IPV4_FRAGMENT = "-c http.curlOptions=-4 "


def sandbox_git_force_ipv4_enabled() -> bool:
    """Whether to pass ``-c http.curlOptions=-4`` to git (libcurl IPv4-only).

    Default **on** (unset env). Set ``SANDBOX_GIT_FORCE_IPV4=0`` to disable.
    """
    raw = os.getenv("SANDBOX_GIT_FORCE_IPV4")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def inject_git_curl_ipv4(command: str) -> str:
    """Insert IPv4 curl option after the main ``git`` invocation (clone/pull in sandbox)."""
    if not sandbox_git_force_ipv4_enabled():
        return command
    if command.startswith("git "):
        return f"git {_GIT_CURL_IPV4_FRAGMENT}{command[4:]}"
    needle = "&& git "
    if needle in command:
        return command.replace(needle, f"&& git {_GIT_CURL_IPV4_FRAGMENT}", 1)
    return command


def sandbox_git_clone_depth_args() -> str:
    """Extra ``git clone`` args when ``SANDBOX_GIT_CLONE_DEPTH`` is set.

    Empty/unset = full history (default). Set to ``1`` for shallow clone — much
    faster for large Azure DevOps/GitHub repos and helps avoid Daytona/sandbox
    command timeouts on slow links.
    """
    raw = os.getenv("SANDBOX_GIT_CLONE_DEPTH", "").strip()
    if not raw:
        return ""
    try:
        n = int(raw)
        if n < 1:
            return ""
        return f" --depth {n}"
    except ValueError:
        logger.warning(
            "Invalid SANDBOX_GIT_CLONE_DEPTH=%r; using full clone",
            raw,
        )
        return ""


def sandbox_git_timeout_seconds() -> int:
    """Max seconds for git clone/pull in sandbox.

    Set ``SANDBOX_GIT_TIMEOUT_SEC`` (default 900). Large repos or slow links may
    need a higher value than the backend default (often 300s).
    """
    raw = os.getenv("SANDBOX_GIT_TIMEOUT_SEC", str(_DEFAULT_GIT_TIMEOUT_SEC)).strip()
    try:
        return max(_MIN_GIT_TIMEOUT_SEC, int(raw))
    except ValueError:
        logger.warning(
            "Invalid SANDBOX_GIT_TIMEOUT_SEC=%r; using default %s",
            raw,
            _DEFAULT_GIT_TIMEOUT_SEC,
        )
        return _DEFAULT_GIT_TIMEOUT_SEC


def execute_sandbox_git(sandbox_backend: SandboxBackendProtocol, command: str) -> ExecuteResponse:
    """Run a git-related shell command in the sandbox with an explicit timeout.

    Falls back to ``execute(command)`` if the backend does not accept ``timeout=``
    (e.g. older test doubles).
    """
    command = inject_git_curl_ipv4(command)
    timeout = sandbox_git_timeout_seconds()
    try:
        return sandbox_backend.execute(command, timeout=timeout)
    except TypeError:
        return sandbox_backend.execute(command)


def git_command_failure_hints(*, git_output: str, is_azure_devops: bool) -> str:
    """Return short, actionable hints for common git HTTPS failures."""
    out = git_output or ""
    hints: list[str] = []

    if is_azure_devops and (
        "TF401019" in out or "does not exist or you do not have permissions" in out
    ):
        hints.append(
            "Azure DevOps: confirm org/project/repo in Azure Repos and that the PAT has "
            "Code (Read) (Work Items API alone is not enough for git)."
        )
    if "timeout" in out.lower() or "timed out" in out.lower():
        hints.append(
            f"Sandbox git hit a timeout; try increasing SANDBOX_GIT_TIMEOUT_SEC "
            f"(currently {sandbox_git_timeout_seconds()})."
        )
    if "Could not resolve host" in out or "Name or service not known" in out:
        hints.append("Network/DNS from sandbox: check egress and git host reachability.")
    if "resolving timed out" in out.lower():
        hints.append(
            "Often IPv6 DNS (AAAA) without working IPv6 egress; keep SANDBOX_GIT_FORCE_IPV4 on "
            "(default) so git uses -c http.curlOptions=-4, or test curl -4 vs curl -6 to dev.azure.com."
        )
    if "SSL" in out or "certificate" in out.lower():
        hints.append("TLS/certificate problem between sandbox and git host.")

    if not hints:
        return ""
    return " Hint: " + " ".join(hints)
