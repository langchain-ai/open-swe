"""GitHub API and git utilities."""

from __future__ import annotations

import logging
import shlex

import httpx
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

from .git_provider import get_credential_url

logger = logging.getLogger(__name__)

# HTTP status codes
HTTP_CREATED = 201
HTTP_UNPROCESSABLE_ENTITY = 422


def _run_git(
    sandbox_backend: SandboxBackendProtocol, repo_dir: str, command: str
) -> ExecuteResponse:
    """Run a git command in the sandbox repo directory."""
    return sandbox_backend.execute(f"cd {repo_dir} && {command}")


def is_valid_git_repo(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    """Check if directory is a valid git repository."""
    git_dir = f"{repo_dir}/.git"
    safe_git_dir = shlex.quote(git_dir)
    result = sandbox_backend.execute(f"test -d {safe_git_dir} && echo exists")
    return result.exit_code == 0 and "exists" in result.output


def remove_directory(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    """Remove a directory and all its contents."""
    safe_repo_dir = shlex.quote(repo_dir)
    result = sandbox_backend.execute(f"rm -rf {safe_repo_dir}")
    return result.exit_code == 0


def git_has_uncommitted_changes(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> bool:
    """Check whether the repo has uncommitted changes."""
    result = _run_git(sandbox_backend, repo_dir, "git status --porcelain")
    return result.exit_code == 0 and bool(result.output.strip())


def git_fetch_origin(sandbox_backend: SandboxBackendProtocol, repo_dir: str) -> ExecuteResponse:
    """Fetch latest from origin (best-effort)."""
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
) -> bool:
    """Checkout branch, creating it if needed."""
    safe_branch = shlex.quote(branch)
    checkout_result = _run_git(sandbox_backend, repo_dir, f"git checkout -B {safe_branch}")
    if checkout_result.exit_code == 0:
        return True
    fallback_create = _run_git(sandbox_backend, repo_dir, f"git checkout -b {safe_branch}")
    if fallback_create.exit_code == 0:
        return True
    fallback = _run_git(sandbox_backend, repo_dir, f"git checkout {safe_branch}")
    return fallback.exit_code == 0


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


_CRED_FILE_PATH = "/tmp/.git-credentials"


def setup_git_credentials(sandbox_backend: SandboxBackendProtocol, github_token: str) -> None:
    """Write git credentials to a temporary file using the sandbox write API.

    The write API sends content in the HTTP body (not via a shell command),
    so the token never appears in shell history or process listings.
    Supports both GitHub and GitLab via the GIT_PROVIDER env var.
    """
    sandbox_backend.write(_CRED_FILE_PATH, get_credential_url(github_token))
    sandbox_backend.execute(f"chmod 600 {_CRED_FILE_PATH}")


def cleanup_git_credentials(sandbox_backend: SandboxBackendProtocol) -> None:
    """Remove the temporary credentials file."""
    sandbox_backend.execute(f"rm -f {_CRED_FILE_PATH}")


def _git_with_credentials(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    command: str,
) -> ExecuteResponse:
    """Run a git command using the temporary credential file."""
    cred_helper = shlex.quote(f"store --file={_CRED_FILE_PATH}")
    return _run_git(sandbox_backend, repo_dir, f"git -c credential.helper={cred_helper} {command}")


def git_push(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    branch: str,
    github_token: str | None = None,
) -> ExecuteResponse:
    """Push the branch to origin, using a token if needed."""
    safe_branch = shlex.quote(branch)
    if not github_token:
        return _run_git(sandbox_backend, repo_dir, f"git push origin {safe_branch}")
    setup_git_credentials(sandbox_backend, github_token)
    try:
        return _git_with_credentials(sandbox_backend, repo_dir, f"push origin {safe_branch}")
    finally:
        cleanup_git_credentials(sandbox_backend)


async def create_github_pr(
    repo_owner: str,
    repo_name: str,
    github_token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> tuple[str | None, int | None, bool]:
    """Create a draft GitHub pull request via the API.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token
        title: PR title
        head_branch: Source branch name
        base_branch: Target branch name
        body: PR description

    Returns:
        Tuple of (pr_url, pr_number, pr_existing) if successful, (None, None, False) otherwise
    """
    pr_payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body,
        "draft": True,
    }

    logger.info(
        "Creating PR: head=%s, base=%s, repo=%s/%s",
        head_branch,
        base_branch,
        repo_owner,
        repo_name,
    )

    async with httpx.AsyncClient() as http_client:
        try:
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
                return pr_url, pr_number, False

            if pr_response.status_code == HTTP_UNPROCESSABLE_ENTITY:
                logger.error("GitHub API validation error (422): %s", pr_data.get("message"))
                existing = await _find_existing_pr(
                    http_client=http_client,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    github_token=github_token,
                    head_branch=head_branch,
                )
                if existing:
                    logger.info("Using existing PR for head branch: %s", existing[0])
                    return existing[0], existing[1], True
            else:
                logger.error(
                    "GitHub API error (%s): %s",
                    pr_response.status_code,
                    pr_data.get("message"),
                )

            if "errors" in pr_data:
                logger.error("GitHub API errors detail: %s", pr_data.get("errors"))

            return None, None, False

        except httpx.HTTPError:
            logger.exception("Failed to create PR via GitHub API")
            return None, None, False


async def _find_existing_pr(
    http_client: httpx.AsyncClient,
    repo_owner: str,
    repo_name: str,
    github_token: str,
    head_branch: str,
) -> tuple[str | None, int | None]:
    """Find an existing PR for the given head branch."""
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    head_ref = f"{repo_owner}:{head_branch}"
    for state in ("open", "all"):
        response = await http_client.get(
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls",
            headers=headers,
            params={"head": head_ref, "state": state, "per_page": 1},
        )
        if response.status_code != 200:  # noqa: PLR2004
            continue
        data = response.json()
        if not data:
            continue
        pr = data[0]
        return pr.get("html_url"), pr.get("number")
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
