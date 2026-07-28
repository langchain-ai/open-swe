"""Docker sandbox backend: one container per run on the local daemon (OPE-7).

SPEEDBAY org-layer file — upstream does not own it. Replaces the no-isolation
``local`` backend: agent commands run inside a container booted from
``openswe-sandbox:dev`` (see ``speedbay/docker/Dockerfile.sandbox``), so a run
cannot read host paths, host env, or host credentials.

Because ``agent/utils/github_proxy.py`` is LangSmith-only, this backend also
owns git auth for its containers: at create (and again at reconnect) it mints a
GitHub App installation token on the **host** via ``speedbay/mint_token.py``
and provisions the container with a token file, a ``gh`` shim that overrides
the prompt's hardcoded ``GH_TOKEN=dummy``, a read-only gitconfig whose
credential helper reads the token file, and the ``commit-msg`` hook that strips
AI attribution. The App private key never enters the container — only a
one-hour installation token scoped to the repos the App is installed on.

Lifecycle mirrors upstream semantics: no ``sandbox_id`` → new container
(fresh ``/workspace``, which is what closes OPE-22 for this backend);
``sandbox_id`` present → reconnect to the same container so follow-up comments
on a thread keep their state. Expired containers are swept lazily on every
factory call — no cleanup daemon.

ponytail: shells out to the docker CLI instead of docker-py — the CLI is
already a hard dependency of the operator's machine, streams stdin/stdout
natively, and saves a pinned SDK. Swap in docker-py if we ever need events.
"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import sys
import time
import uuid

from deepagents.backends.sandbox import (
    MAX_OUTPUT_BYTES,
    BaseSandbox,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)

IMAGE_ENV = "DOCKER_SANDBOX_IMAGE"
DEFAULT_IMAGE = "openswe-sandbox:dev"
TTL_ENV = "DOCKER_SANDBOX_TTL_SECONDS"
DEFAULT_TTL_SECONDS = 24 * 3600
DEFAULT_EXECUTE_TIMEOUT = 300

_LABEL = "openswe.sandbox"
_CREATED_LABEL = "openswe.created"
_TOKEN_PATH = "/opt/speedbay/token"
_GITCONFIG_PATH = "/opt/speedbay/gitconfig"
_HOOKS_DIR = "/opt/speedbay/githooks"

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_GH_SHIM = """#!/bin/sh
# Replaces the hardcoded GH_TOKEN=dummy from the agent prompt with the real
# installation token provisioned by the docker backend.
GH_TOKEN="$(cat /opt/speedbay/token 2>/dev/null)" exec /usr/bin/gh "$@"
"""

_GITCONFIG = """[user]
\tname = open-swe
\temail = open-swe@speedbay.com
[credential "https://github.com"]
\thelper = "!f() { echo username=x-access-token; echo password=$(cat /opt/speedbay/token); }; f"
[core]
\thooksPath = /opt/speedbay/githooks
[init]
\tdefaultBranch = main
[safe]
\tdirectory = *
"""


def _image() -> str:
    return os.getenv(IMAGE_ENV, DEFAULT_IMAGE)


def _docker(
    *args: str,
    input_bytes: bytes | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run a docker CLI command, capturing bytes; never raises on non-zero exit."""
    return subprocess.run(
        ["docker", *args],
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
    )


def _mint_token() -> str:
    """Mint a GitHub App installation token on the host (see module docstring)."""
    proc = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "speedbay" / "mint_token.py")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"token mint failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


class DockerSandbox(BaseSandbox):
    """``SandboxBackendProtocol`` over ``docker exec`` against one container."""

    def __init__(self, container: str) -> None:
        self._container = container

    @property
    def id(self) -> str:
        """Container name; upstream persists it per-thread and passes it back for reconnect."""
        return self._container

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run ``command`` through ``bash -lc`` inside the container.

        Combined stdout+stderr, truncated at deepagents' ``MAX_OUTPUT_BYTES``.
        A timeout returns exit code 124 (the ``timeout(1)`` convention) rather
        than raising, so the agent sees a normal failed command.
        """
        effective = timeout if timeout and timeout > 0 else DEFAULT_EXECUTE_TIMEOUT
        try:
            proc = _docker(
                "exec", self._container, "bash", "-lc", command,
                timeout=effective + 10,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Command timed out after {effective}s", exit_code=124
            )
        raw = proc.stdout + proc.stderr
        truncated = len(raw) > MAX_OUTPUT_BYTES
        return ExecuteResponse(
            output=raw[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
            exit_code=proc.returncode,
            truncated=truncated,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write files via ``docker exec`` stdin; partial success per protocol."""
        results: list[FileUploadResponse] = []
        for path, data in files:
            parent = shlex.quote(os.path.dirname(path) or ".")
            proc = _docker(
                "exec", "-i", self._container, "sh", "-c",
                f"mkdir -p {parent} && cat > {shlex.quote(path)}",
                input_bytes=data,
                timeout=120,
            )
            error = None if proc.returncode == 0 else proc.stderr.decode("utf-8", "replace")[:200]
            results.append(FileUploadResponse(path=path, error=error))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read files via ``docker exec cat``; binary-safe, partial success."""
        results: list[FileDownloadResponse] = []
        for path in paths:
            proc = _docker(
                "exec", self._container, "cat", path,
                timeout=120,
            )
            if proc.returncode == 0:
                results.append(FileDownloadResponse(path=path, content=proc.stdout))
            else:
                stderr = proc.stderr.decode("utf-8", "replace")
                error = "file_not_found" if "No such file" in stderr else stderr[:200]
                results.append(FileDownloadResponse(path=path, content=None, error=error))
        return results


def _provision(container: str) -> None:
    """Install token, gh shim, gitconfig, and commit-msg hook into the container.

    Idempotent; run at create and again at reconnect (the reconnect run
    refreshes the one-hour token).
    """
    token = _mint_token()
    hook = (_REPO_ROOT / "speedbay" / "githooks" / "commit-msg").read_bytes()
    token_script = (
        f"mkdir -p {_HOOKS_DIR} && "
        f"cat > {_TOKEN_PATH}.new && mv {_TOKEN_PATH}.new {_TOKEN_PATH} && chmod 600 {_TOKEN_PATH}"
    )
    steps: list[tuple[str, bytes]] = [
        (token_script, token.encode()),
        ("cat > /usr/local/bin/gh && chmod 755 /usr/local/bin/gh", _GH_SHIM.encode()),
        (f"cat > {_GITCONFIG_PATH} && chmod 444 {_GITCONFIG_PATH}", _GITCONFIG.encode()),
        (f"cat > {_HOOKS_DIR}/commit-msg && chmod 755 {_HOOKS_DIR}/commit-msg", hook),
    ]
    for cmd, data in steps:
        proc = _docker("exec", "-i", container, "sh", "-c", cmd, input_bytes=data, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(
                f"provisioning failed in {container}: "
                f"{proc.stderr.decode('utf-8', 'replace')[:300]}"
            )


def _sweep_expired() -> None:
    """Remove sandbox containers older than the TTL. Lazy, best-effort."""
    ttl = int(os.getenv(TTL_ENV, str(DEFAULT_TTL_SECONDS)))
    proc = _docker(
        "ps", "-a", "--filter", f"label={_LABEL}",
        "--format", f'{{{{.Names}}}} {{{{.Label "{_CREATED_LABEL}"}}}}',
    )
    if proc.returncode != 0:
        return
    now = time.time()
    for line in proc.stdout.decode().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        name, created = parts
        try:
            expired = now - float(created) > ttl
        except ValueError:
            expired = True  # unparseable label: reap rather than leak
        if expired:
            _docker("rm", "-f", name)


def create_docker_sandbox(sandbox_id: str | None = None) -> DockerSandbox:
    """Create a new sandbox container or reconnect to an existing one.

    Args:
        sandbox_id: Existing container name → ``docker start`` it (no-op when
            already running) and refresh its token. ``None`` → run a fresh
            container from the configured image.

    Returns:
        DockerSandbox bound to the container.
    """
    _sweep_expired()

    if sandbox_id:
        proc = _docker("start", sandbox_id)
        if proc.returncode != 0:
            raise RuntimeError(
                f"cannot reconnect to sandbox {sandbox_id}: "
                f"{proc.stderr.decode('utf-8', 'replace')[:200]}"
            )
        _provision(sandbox_id)
        return DockerSandbox(sandbox_id)

    name = f"openswe-{uuid.uuid4().hex[:12]}"
    proc = _docker(
        "run", "-d",
        "--name", name,
        "--label", f"{_LABEL}=1",
        "--label", f"{_CREATED_LABEL}={int(time.time())}",
        "--env", f"GIT_CONFIG_GLOBAL={_GITCONFIG_PATH}",
        "--workdir", "/workspace",
        "--pids-limit", "2048",
        "--memory", os.getenv("DOCKER_SANDBOX_MEMORY", "4g"),
        _image(),
        "sleep", "infinity",
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker run failed: {proc.stderr.decode('utf-8', 'replace')[:300]}"
        )
    _provision(name)
    return DockerSandbox(name)


def validate_startup_config() -> None:
    """Fail at boot — not on first run — when docker is unusable (OPE-7 step 4).

    Raises:
        ValueError: daemon unreachable or the sandbox image is absent.
    """
    proc = _docker("version", "--format", "{{.Server.Version}}")
    if proc.returncode != 0:
        raise ValueError(
            "SANDBOX_TYPE=docker but the Docker daemon is unreachable: "
            f"{proc.stderr.decode('utf-8', 'replace')[:200]}"
        )
    image = _image()
    proc = _docker("image", "inspect", image)
    if proc.returncode != 0:
        raise ValueError(
            f"SANDBOX_TYPE=docker but image '{image}' is not present. Build it: "
            "docker build -f speedbay/docker/Dockerfile.sandbox -t "
            f"{image} speedbay/docker"
        )
