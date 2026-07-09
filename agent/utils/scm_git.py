"""Git helpers for Azure DevOps HTTPS clone/push inside sandboxes."""

from __future__ import annotations

import base64
import logging
import shlex

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

logger = logging.getLogger(__name__)

AZURE_DEVOPS_HTTP_GITCONFIG_PATH = "/tmp/.open-swe-ado.gitconfig"


def azure_devops_git_c_http_extra_header(pat: str) -> str:
    """Return a shell-quoted ``git -c`` value for HTTPS Basic auth (``:PAT``)."""
    b64 = base64.b64encode(f":{pat}".encode()).decode("ascii")
    return shlex.quote(f"http.extraHeader=Authorization: Basic {b64}")


def _run_git(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, command: str
) -> ExecuteResponse:
    safe_repo_dir = shlex.quote(repo_dir)
    return sandbox_backend.execute(f"cd {safe_repo_dir} && {command}")


def is_valid_git_repo(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    git_dir = f"{repo_dir}/.git"
    safe_git_dir = shlex.quote(git_dir)
    result = sandbox_backend.execute(f"test -d {safe_git_dir} && echo exists")
    return result.exit_code == 0 and "exists" in result.output


def remove_directory(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    safe_repo_dir = shlex.quote(repo_dir)
    result = sandbox_backend.execute(f"rm -rf {safe_repo_dir}")
    return result.exit_code == 0


def git_has_uncommitted_changes(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    result = _run_git(sandbox_backend, repo_dir, "git status --porcelain")
    return result.exit_code == 0 and bool(result.output.strip())
