"""GitHub API and git utilities."""

from __future__ import annotations

import logging
import shlex
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# HTTP status codes
HTTP_CREATED = 201
HTTP_UNPROCESSABLE_ENTITY = 422


def _run_git(sandbox_backend: Any, repo_dir: str, command: str) -> Any:
    """Run a git command in the sandbox repo directory."""
    return sandbox_backend.execute(f"cd {repo_dir} && {command}")


def repo_directory_exists(sandbox_backend: Any, repo_dir: str) -> bool:
    """Check if repository directory exists."""
    safe_repo_dir = shlex.quote(repo_dir)
    result = sandbox_backend.execute(f"test -d {safe_repo_dir} && echo exists")
    return result.exit_code == 0 and "exists" in result.output


def is_valid_git_repo(sandbox_backend: Any, repo_dir: str) -> bool:
    """Check if directory is a valid git repository."""
    git_dir = f"{repo_dir}/.git"
    safe_git_dir = shlex.quote(git_dir)
    result = sandbox_backend.execute(f"test -d {safe_git_dir} && echo exists")
    return result.exit_code == 0 and "exists" in result.output


def remove_directory(sandbox_backend: Any, repo_dir: str) -> bool:
    """Remove a directory and all its contents."""
    safe_repo_dir = shlex.quote(repo_dir)
    result = sandbox_backend.execute(f"rm -rf {safe_repo_dir}")
    return result.exit_code == 0


def git_has_uncommitted_changes(sandbox_backend: Any, repo_dir: str) -> bool:
    """Check whether the repo has uncommitted changes."""
    result = _run_git(sandbox_backend, repo_dir, "git status --porcelain")
    return result.exit_code == 0 and bool(result.output.strip())


def git_fetch_origin(sandbox_backend: Any, repo_dir: str) -> Any:
    """Fetch latest from origin (best-effort)."""
    return _run_git(sandbox_backend, repo_dir, "git fetch origin 2>/dev/null || true")


def git_has_unpushed_commits(sandbox_backend: Any, repo_dir: str) -> bool:
    """Check whether there are commits not pushed to upstream."""
    git_log_cmd = (
        "git log --oneline @{upstream}..HEAD 2>/dev/null "
        "|| git log --oneline origin/HEAD..HEAD 2>/dev/null || echo ''"
    )
    result = _run_git(sandbox_backend, repo_dir, git_log_cmd)
    return result.exit_code == 0 and bool(result.output.strip())


def git_current_branch(sandbox_backend: Any, repo_dir: str) -> str:
    """Get the current git branch name."""
    result = _run_git(sandbox_backend, repo_dir, "git rev-parse --abbrev-ref HEAD")
    return result.output.strip() if result.exit_code == 0 else ""


def git_checkout_branch(sandbox_backend: Any, repo_dir: str, branch: str) -> bool:
    """Checkout branch, creating it if needed."""
    safe_branch = shlex.quote(branch)
    checkout_result = _run_git(
        sandbox_backend, repo_dir, f"git checkout -b {safe_branch}"
    )
    if checkout_result.exit_code == 0:
        return True
    fallback = _run_git(sandbox_backend, repo_dir, f"git checkout {safe_branch}")
    return fallback.exit_code == 0


def git_config_user(
    sandbox_backend: Any,
    repo_dir: str,
    name: str,
    email: str,
) -> None:
    """Configure git user name and email."""
    safe_name = shlex.quote(name)
    safe_email = shlex.quote(email)
    _run_git(sandbox_backend, repo_dir, f"git config user.name {safe_name}")
    _run_git(sandbox_backend, repo_dir, f"git config user.email {safe_email}")


def git_add_all(sandbox_backend: Any, repo_dir: str) -> Any:
    """Stage all changes."""
    return _run_git(sandbox_backend, repo_dir, "git add -A")


def git_commit(sandbox_backend: Any, repo_dir: str, message: str) -> Any:
    """Commit staged changes with the given message."""
    safe_message = shlex.quote(message)
    return _run_git(sandbox_backend, repo_dir, f"git commit -m {safe_message}")


def git_get_remote_url(sandbox_backend: Any, repo_dir: str) -> str | None:
    """Get the origin remote URL."""
    result = _run_git(sandbox_backend, repo_dir, "git remote get-url origin")
    if result.exit_code != 0:
        return None
    return result.output.strip()


def git_push(
    sandbox_backend: Any,
    repo_dir: str,
    branch: str,
    github_token: str | None = None,
) -> Any:
    """Push the branch to origin, using a token if needed."""
    safe_branch = shlex.quote(branch)
    remote_url = git_get_remote_url(sandbox_backend, repo_dir)
    if remote_url and "github.com" in remote_url and "@" not in remote_url and github_token:
        auth_url = remote_url.replace("https://", f"https://git:{github_token}@")
        return _run_git(sandbox_backend, repo_dir, f"git push {auth_url} {safe_branch}")
    return _run_git(sandbox_backend, repo_dir, f"git push origin {safe_branch}")


async def create_github_pr(
    repo_owner: str,
    repo_name: str,
    github_token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> tuple[str | None, int | None]:
    """Create a GitHub pull request via the API.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token
        title: PR title
        head_branch: Source branch name
        base_branch: Target branch name
        body: PR description

    Returns:
        Tuple of (pr_url, pr_number) if successful, (None, None) otherwise
    """
    pr_payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body,
    }

    logger.info(
        "Creating PR: head=%s, base=%s, repo=%s/%s",
        head_branch,
        base_branch,
        repo_owner,
        repo_name,
    )

    try:
        async with httpx.AsyncClient() as http_client:
            pr_response = await http_client.post(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=pr_payload,
            )

            pr_data = pr_response.json()

            if pr_response.status_code == HTTP_CREATED:
                pr_url = pr_data.get("html_url")
                pr_number = pr_data.get("number")
                logger.info("PR created successfully: %s", pr_url)
                return pr_url, pr_number

            if pr_response.status_code == HTTP_UNPROCESSABLE_ENTITY:
                logger.error("GitHub API validation error (422): %s", pr_data.get("message"))
            else:
                logger.error(
                    "GitHub API error (%s): %s",
                    pr_response.status_code,
                    pr_data.get("message"),
                )

            if "errors" in pr_data:
                logger.error("GitHub API errors detail: %s", pr_data.get("errors"))

            return None, None

    except httpx.HTTPError:
        logger.exception("Failed to create PR via GitHub API")
        return None, None


async def get_github_default_branch(
    repo_owner: str,
    repo_name: str,
    github_token: str,
) -> str:
    """Get the default branch of a GitHub repository via the API.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token

    Returns:
        The default branch name (e.g., "main" or "master")
    """
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

            if response.status_code == 200:  # noqa: PLR2004
                repo_data = response.json()
                default_branch = repo_data.get("default_branch", "main")
                logger.debug("Got default branch from GitHub API: %s", default_branch)
                return default_branch

            logger.warning(
                "Failed to get repo info from GitHub API (%s), falling back to 'main'",
                response.status_code,
            )
            return "main"

    except httpx.HTTPError:
        logger.exception("Failed to get default branch from GitHub API, falling back to 'main'")
        return "main"
