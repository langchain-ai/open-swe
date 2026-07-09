"""Git helpers for Azure DevOps HTTPS clone/push inside sandboxes."""

from __future__ import annotations

import base64
import logging
import re
import shlex

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

logger = logging.getLogger(__name__)

# ADO branch short names must not contain shell metacharacters (defense in depth).
_SAFE_GIT_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def validate_git_branch_short_name(branch: str) -> str:
    """Return a stripped branch name or raise if it is unsafe for shell interpolation."""
    name = branch.strip()
    if not name or not _SAFE_GIT_BRANCH_RE.fullmatch(name):
        msg = f"Unsafe git branch name: {branch!r}"
        raise ValueError(msg)
    return name


def azure_devops_git_c_http_extra_header(pat: str) -> str:
    """Return a shell-quoted ``git -c`` value for HTTPS Basic auth (``:PAT``)."""
    b64 = base64.b64encode(f":{pat}".encode()).decode("ascii")
    return shlex.quote(f"http.extraHeader=Authorization: Basic {b64}")


def inject_azure_devops_git_auth(command: str, pat: str | None) -> str:
    """Prefix each ``git`` invocation in a shell command with transient ``-c http.extraHeader``.

    Credentials are never written to ``.git/config`` or other sandbox files; the server
    injects auth at command execution time (same pattern as clone/fetch in ``scm_clone``).
    """
    if not pat:
        return command
    c_arg = azure_devops_git_c_http_extra_header(pat)
    fragment = f"-c {c_arg} "
    return re.sub(r"(^|&& |; )git ", rf"\1git {fragment}", command)


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
