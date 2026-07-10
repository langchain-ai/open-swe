"""Lightning AI sandbox backend integration.

Uses the Lightning Python SDK (``lightning-sdk``) to create or resume durable
CPU sandboxes and adapts them to deepagents' ``SandboxBackendProtocol`` via
``BaseSandbox``.

Auth (first match wins):
  - ``LIGHTNING_SANDBOX_API_KEY``
  - ``LIGHTNING_API_KEY``

Optional env:
  - ``LIGHTNING_CLOUD_URL`` — API host (default ``https://lightning.ai``)
  - ``LIGHTNING_SANDBOX_INSTANCE_TYPE`` — default ``cpu-1``
  - ``LIGHTNING_SANDBOX_TIMEOUT_MS`` — sandbox lifetime in milliseconds
  - ``LIGHTNING_SANDBOX_STORAGE_GB`` — writable disk size (CPU only)
  - ``LIGHTNING_SANDBOX_NETWORK_POLICY`` — ``allow-all`` (default) or ``deny-all``
  - ``LIGHTNING_SANDBOX_PERSISTENT`` — ``true``/``false`` (default ``true``)
  - ``LIGHTNING_SANDBOX_WORKDIR`` — command cwd (default ``/workspace``)
  - ``LIGHTNING_SANDBOX_RUNTIME`` — curated runtime id (when set)
  - ``LIGHTNING_SANDBOX_IMAGE`` — custom OCI image (mutually exclusive with runtime)
  - ``LIGHTNING_SANDBOX_BOOTSTRAP`` — install python3/git when missing (default ``true``)
"""

from __future__ import annotations

import base64
import logging
import os
import posixpath
import shlex
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from lightning_sdk.sandbox import (
    RunCommandOpts,
    Sandbox,
    SandboxConfig,
    SandboxInstance,
)

logger = logging.getLogger(__name__)

DEFAULT_WORKDIR = "/workspace"
DEFAULT_INSTANCE_TYPE = "cpu-1"
DEFAULT_NETWORK_POLICY = "allow-all"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30 * 60
TIMEOUT_EXIT_CODE = 124
_RESUME_STATUSES = frozenset({"paused", "stopped", "idle"})
_RUNNING_STATUSES = frozenset({"running", "ready"})


def _api_key_from_env() -> str | None:
    return os.getenv("LIGHTNING_SANDBOX_API_KEY") or os.getenv("LIGHTNING_API_KEY") or None


def _truthy(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _sandbox_config() -> SandboxConfig:
    api_key = _api_key_from_env()
    if not api_key:
        raise ValueError(
            "LIGHTNING_API_KEY or LIGHTNING_SANDBOX_API_KEY environment variable is required"
        )
    base_url = os.getenv("LIGHTNING_CLOUD_URL") or None
    return SandboxConfig(api_key=api_key, base_url=base_url)


def _ensure_workspace(sandbox: SandboxInstance, workdir: str) -> None:
    try:
        sandbox.mkdir(workdir)
    except Exception:
        # mkdir may fail if the path already exists; verify with a no-op command.
        result = sandbox.run_command(
            RunCommandOpts(cmd="bash", args=["-lc", f"mkdir -p -- {shlex.quote(workdir)}"])
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(f"Failed to create workspace {workdir}: {result.output}") from None


def _run_sync(sandbox: SandboxInstance, script: str) -> Any:
    """Run a shell script server-side (blocking; no client-side detach poll)."""
    return sandbox.run_command(RunCommandOpts(cmd="bash", args=["-lc", script]))


# deepagents BaseSandbox implements read/write/edit/glob/grep via in-sandbox
# ``python3 -c ...`` helpers. The default Lightning CPU image (node24) does not
# ship Python, so we install a minimal tool chain once per sandbox.
_BOOTSTRAP_SCRIPT = """
set -euo pipefail
need_python=0
need_git=0
command -v python3 >/dev/null 2>&1 || need_python=1
command -v git >/dev/null 2>&1 || need_git=1
if [ "$need_python" -eq 0 ] && [ "$need_git" -eq 0 ]; then
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  pkgs=""
  [ "$need_python" -eq 1 ] && pkgs="$pkgs python3"
  [ "$need_git" -eq 1 ] && pkgs="$pkgs git"
  # shellcheck disable=SC2086
  apt-get install -y -qq $pkgs
elif command -v apk >/dev/null 2>&1; then
  pkgs=""
  [ "$need_python" -eq 1 ] && pkgs="$pkgs python3"
  [ "$need_git" -eq 1 ] && pkgs="$pkgs git"
  # shellcheck disable=SC2086
  apk add --no-cache $pkgs
else
  echo "No supported package manager; cannot bootstrap python3/git" >&2
  exit 1
fi
command -v python3 >/dev/null 2>&1
"""


def _bootstrap_toolchain(sandbox: SandboxInstance) -> None:
    """Ensure python3 (required by deepagents file tools) and git are present."""
    if not _truthy(os.getenv("LIGHTNING_SANDBOX_BOOTSTRAP"), default=True):
        return
    probe = _run_sync(sandbox, "command -v python3 && command -v git")
    if probe.exit_code in (0, None):
        return
    logger.info(
        "Bootstrapping python3/git on Lightning sandbox %s",
        sandbox.sandbox_id,
    )
    result = _run_sync(sandbox, _BOOTSTRAP_SCRIPT)
    if result.exit_code not in (0, None):
        raise RuntimeError(
            "Failed to bootstrap Lightning sandbox toolchain (python3/git). "
            f"exit={result.exit_code} output={result.output}"
        )


def _reconnect_or_resume(client: Sandbox, sandbox_id: str) -> SandboxInstance:
    """Fetch an existing sandbox and resume it when stopped/paused."""
    sandbox = client.get(sandbox_id)
    status = (sandbox.status or "").lower()
    if status in _RUNNING_STATUSES:
        return sandbox
    if status in _RESUME_STATUSES or sandbox.persistent:
        logger.info("Resuming Lightning sandbox %s (status=%s)", sandbox_id, status or "unknown")
        return sandbox.resume()
    raise RuntimeError(
        f"Lightning sandbox {sandbox_id} is not resumable (status={status or 'unknown'})"
    )


def create_lightning_sandbox(sandbox_id: str | None = None) -> LightningSandbox:
    """Create or reconnect to a Lightning AI sandbox.

    Args:
        sandbox_id: Optional existing sandbox ID to reconnect/resume.
            If None, creates a new sandbox.

    Returns:
        LightningSandbox implementing SandboxBackendProtocol.
    """
    client = Sandbox(config=_sandbox_config())
    workdir = os.getenv("LIGHTNING_SANDBOX_WORKDIR", DEFAULT_WORKDIR).strip() or DEFAULT_WORKDIR

    if sandbox_id:
        sandbox = _reconnect_or_resume(client, sandbox_id)
    else:
        instance_type = (
            os.getenv("LIGHTNING_SANDBOX_INSTANCE_TYPE", DEFAULT_INSTANCE_TYPE).strip()
            or DEFAULT_INSTANCE_TYPE
        )
        network_policy = (
            os.getenv("LIGHTNING_SANDBOX_NETWORK_POLICY", DEFAULT_NETWORK_POLICY).strip()
            or DEFAULT_NETWORK_POLICY
        )
        persistent = _truthy(os.getenv("LIGHTNING_SANDBOX_PERSISTENT"), default=True)
        timeout_ms = _optional_int("LIGHTNING_SANDBOX_TIMEOUT_MS")
        storage_gb = _optional_int("LIGHTNING_SANDBOX_STORAGE_GB")
        runtime = (os.getenv("LIGHTNING_SANDBOX_RUNTIME") or "").strip() or None
        image = (os.getenv("LIGHTNING_SANDBOX_IMAGE") or "").strip() or None
        if runtime and image:
            raise ValueError(
                "Set only one of LIGHTNING_SANDBOX_RUNTIME and LIGHTNING_SANDBOX_IMAGE"
            )

        create_kwargs: dict[str, Any] = {
            "name": f"open-swe-{os.getpid()}",
            "instance_type": instance_type,
            "persistent": persistent,
            "network_policy": network_policy,
        }
        if timeout_ms is not None:
            create_kwargs["timeout"] = timeout_ms
        if storage_gb is not None:
            create_kwargs["storage_gb"] = storage_gb
        if runtime is not None:
            create_kwargs["runtime"] = runtime
        if image is not None:
            create_kwargs["image"] = image

        sandbox = client.create(**create_kwargs)

    _ensure_workspace(sandbox, workdir)
    _bootstrap_toolchain(sandbox)
    return LightningSandbox(sandbox=sandbox, workdir=workdir)


class LightningSandbox(BaseSandbox):
    """deepagents sandbox backend backed by a Lightning ``SandboxInstance``."""

    def __init__(
        self,
        *,
        sandbox: SandboxInstance,
        workdir: str = DEFAULT_WORKDIR,
        timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self._sandbox = sandbox
        self._workdir = workdir
        self._default_timeout = timeout

    @property
    def id(self) -> str:
        return self._sandbox.sandbox_id

    @property
    def sandbox(self) -> SandboxInstance:
        return self._sandbox

    def get_work_dir(self) -> str:
        """Preferred writable work directory for Open SWE repo operations."""
        return self._workdir

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout is not None and effective_timeout < 0:
            raise ValueError(f"timeout must be non-negative, got {effective_timeout}")

        opts = RunCommandOpts(
            cmd="bash",
            args=["-lc", command],
            cwd=self._workdir,
        )

        # Lightning's run_command has no server-side command timeout; use
        # detached + client-side wait when a positive timeout is requested.
        if effective_timeout and effective_timeout > 0:
            opts.detached = True
            handle = self._sandbox.run_command(opts)
            cmd_id = handle.cmd_id
            try:
                status = self._sandbox.wait_for_command(
                    cmd_id,
                    timeout=float(effective_timeout),
                )
            except TimeoutError:
                try:
                    self._sandbox.kill_command(cmd_id)
                except Exception:
                    logger.debug(
                        "Failed to kill timed-out Lightning command %s", cmd_id, exc_info=True
                    )
                return ExecuteResponse(
                    output=f"Command timed out after {effective_timeout} seconds",
                    exit_code=TIMEOUT_EXIT_CODE,
                    truncated=False,
                )
            return ExecuteResponse(
                output=status.output or "",
                exit_code=status.exit_code if status.exit_code is not None else 0,
                truncated=False,
            )

        handle = self._sandbox.run_command(opts)
        return ExecuteResponse(
            output=handle.output or "",
            exit_code=handle.exit_code if handle.exit_code is not None else 0,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [self._write_file(path, content) for path, content in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [self._read_file(path) for path in paths]

    def _write_file(self, path: str, content: bytes) -> FileUploadResponse:
        if not path.startswith("/"):
            return FileUploadResponse(path=path, error="invalid_path")

        parent = posixpath.dirname(path)
        try:
            if parent and parent != "/":
                self._sandbox.run_command(
                    RunCommandOpts(
                        cmd="bash",
                        args=["-lc", f"mkdir -p -- {shlex.quote(parent)}"],
                    )
                )
            # Prefer the files API for text; fall back to base64 for binary.
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                b64 = base64.b64encode(content).decode("ascii")
                result = self._sandbox.run_command(
                    RunCommandOpts(
                        cmd="bash",
                        args=[
                            "-lc",
                            f"printf %s {shlex.quote(b64)} | base64 -d > {shlex.quote(path)}",
                        ],
                    )
                )
                if result.exit_code not in (0, None):
                    return FileUploadResponse(path=path, error="permission_denied")
                return FileUploadResponse(path=path, error=None)

            self._sandbox.write_file(path, text)
            return FileUploadResponse(path=path, error=None)
        except Exception as exc:
            logger.debug("Lightning upload failed for %s: %s", path, exc, exc_info=True)
            msg = str(exc).lower()
            if "not found" in msg or "404" in msg:
                return FileUploadResponse(path=path, error="file_not_found")
            if "permission" in msg or "403" in msg:
                return FileUploadResponse(path=path, error="permission_denied")
            if "directory" in msg:
                return FileUploadResponse(path=path, error="is_directory")
            return FileUploadResponse(path=path, error="invalid_path")

    def _read_file(self, path: str) -> FileDownloadResponse:
        if not path.startswith("/"):
            return FileDownloadResponse(path=path, content=None, error="invalid_path")

        try:
            # Detect directories before reading.
            is_dir = self._sandbox.run_command(
                RunCommandOpts(cmd="bash", args=["-lc", f"test -d {shlex.quote(path)}"])
            )
            if is_dir.exit_code == 0:
                return FileDownloadResponse(path=path, content=None, error="is_directory")

            exists = self._sandbox.run_command(
                RunCommandOpts(cmd="bash", args=["-lc", f"test -e {shlex.quote(path)}"])
            )
            if exists.exit_code != 0:
                return FileDownloadResponse(path=path, content=None, error="file_not_found")

            text = self._sandbox.read_file(path)
            if text is not None:
                return FileDownloadResponse(path=path, content=text.encode("utf-8"), error=None)

            # Binary / non-UTF-8 path via base64.
            result = self._sandbox.run_command(
                RunCommandOpts(
                    cmd="bash",
                    args=[
                        "-lc",
                        f"base64 -w0 -- {shlex.quote(path)} 2>/dev/null || base64 -- {shlex.quote(path)}",
                    ],
                )
            )
            if result.exit_code not in (0, None):
                return FileDownloadResponse(path=path, content=None, error="permission_denied")
            raw = base64.b64decode((result.output or "").strip())
            return FileDownloadResponse(path=path, content=raw, error=None)
        except Exception as exc:
            logger.debug("Lightning download failed for %s: %s", path, exc, exc_info=True)
            msg = str(exc).lower()
            if "not found" in msg or "404" in msg:
                return FileDownloadResponse(path=path, content=None, error="file_not_found")
            if "permission" in msg or "403" in msg:
                return FileDownloadResponse(path=path, content=None, error="permission_denied")
            return FileDownloadResponse(path=path, content=None, error="invalid_path")
