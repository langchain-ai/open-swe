"""Git helpers for sandbox repositories."""

from __future__ import annotations

import shlex

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol


def is_permanent_github_push_failure(output: str) -> bool:
    """Return whether git push output indicates a permanent auth failure."""
    normalized_output = output.lower()
    return (
        "permanent_failure" in normalized_output
        or "403" in normalized_output
        or "permission" in normalized_output
        or "denied" in normalized_output
    )


def _run_git(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, command: str
) -> ExecuteResponse:
    """Run a git command in the sandbox repo directory."""
    safe_repo_dir = shlex.quote(repo_dir)
    return sandbox_backend.execute(f"cd {safe_repo_dir} && {command}")


def git_has_uncommitted_changes(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    """Check whether the repo has uncommitted changes."""
    result = _run_git(sandbox_backend, repo_dir, "git status --porcelain")
    return result.exit_code == 0 and bool(result.output.strip())


def git_fetch_origin(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> ExecuteResponse:
    """Fetch latest from origin."""
    return _run_git(sandbox_backend, repo_dir, "git fetch origin 2>/dev/null || true")


def git_has_unpushed_commits(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    """Check whether there are commits not pushed to upstream."""
    git_log_cmd = (
        "git log --oneline @{upstream}..HEAD 2>/dev/null "
        "|| git log --oneline origin/HEAD..HEAD 2>/dev/null || echo ''"
    )
    result = _run_git(sandbox_backend, repo_dir, git_log_cmd)
    return result.exit_code == 0 and bool(result.output.strip())


def git_current_branch(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> str:
    """Get the current git branch name."""
    result = _run_git(sandbox_backend, repo_dir, "git rev-parse --abbrev-ref HEAD")
    return result.output.strip() if result.exit_code == 0 else ""


def git_checkout_branch(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, branch: str
) -> tuple[bool, str]:
    """Checkout branch, creating it if needed."""
    safe_branch = shlex.quote(branch)
    checkout_result = _run_git(sandbox_backend, repo_dir, f"git checkout -B {safe_branch}")
    if checkout_result.exit_code == 0:
        return True, ""
    fallback_create = _run_git(sandbox_backend, repo_dir, f"git checkout -b {safe_branch}")
    if fallback_create.exit_code == 0:
        return True, ""
    fallback = _run_git(sandbox_backend, repo_dir, f"git checkout {safe_branch}")
    if fallback.exit_code == 0:
        return True, ""
    return False, fallback.output.strip() or checkout_result.output.strip()


def git_checkout_existing_branch(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, branch: str
) -> ExecuteResponse:
    """Checkout an existing branch without creating or resetting it."""
    safe_branch = shlex.quote(branch)
    return _run_git(sandbox_backend, repo_dir, f"git checkout {safe_branch}")


def git_config_user(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    name: str,
    email: str,
) -> None:
    """Configure git user name and email."""
    safe_name = shlex.quote(name)
    safe_email = shlex.quote(email)
    _run_git(sandbox_backend, repo_dir, f"git config user.name {safe_name}")
    _run_git(sandbox_backend, repo_dir, f"git config user.email {safe_email}")


def git_add_all(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> ExecuteResponse:
    """Stage all changes."""
    return _run_git(sandbox_backend, repo_dir, "git add -A")


def git_commit(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, message: str
) -> ExecuteResponse:
    """Commit staged changes with the given message."""
    safe_message = shlex.quote(message)
    return _run_git(sandbox_backend, repo_dir, f"git commit -m {safe_message}")


def git_get_remote_url(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> str | None:
    """Get the origin remote URL."""
    result = _run_git(sandbox_backend, repo_dir, "git remote get-url origin")
    if result.exit_code != 0:
        return None
    return result.output.strip()


def git_push(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    branch: str,
) -> ExecuteResponse:
    """Push the branch to origin."""
    safe_branch = shlex.quote(branch)
    return _run_git(sandbox_backend, repo_dir, f"git push origin {safe_branch}")
