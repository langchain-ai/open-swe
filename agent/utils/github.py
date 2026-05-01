"""GitHub API and git utilities."""

from __future__ import annotations

import logging
import shlex

import httpx
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol

logger = logging.getLogger(__name__)

# HTTP status codes
HTTP_CREATED = 201
HTTP_UNPROCESSABLE_ENTITY = 422


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
    """Push the branch to origin.

    Authentication is handled by the sandbox proxy (configured at sandbox creation
    time via the LangSmith proxy-config API), so no token is needed here.
    """
    safe_branch = shlex.quote(branch)
    return _run_git(sandbox_backend, repo_dir, f"git push origin {safe_branch}")


async def create_github_pr(
    repo_owner: str,
    repo_name: str,
    github_token: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
    installation_token: str | None = None,
) -> tuple[str | None, int | None, bool]:
    """Create a draft GitHub pull request via the API.

    When *github_token* differs from *installation_token* (e.g. a user
    OAuth token), the function first attempts to create the PR with the
    user token so the user becomes the PR author.  If that fails it
    retries with the installation token.  The ``OpenSWE`` label is
    always added using the installation token.

    Args:
        repo_owner: Repository owner (e.g., "langchain-ai")
        repo_name: Repository name (e.g., "deepagents")
        github_token: GitHub access token (user token preferred)
        title: PR title
        head_branch: Source branch name
        base_branch: Target branch name
        body: PR description
        installation_token: GitHub App installation token used for labeling and as a fallback
            for PR creation. Falls back to github_token when not provided.

    Returns:
        Tuple of (pr_url, pr_number, pr_existing) if successful, (None, None, False) otherwise
    """
    tokens_to_try = [github_token]
    if installation_token and installation_token != github_token:
        tokens_to_try.append(installation_token)
    label_tok = installation_token or github_token

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
        for token in tokens_to_try:
            try:
                pr_response = await http_client.post(
                    f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=pr_payload,
                )

                pr_data = pr_response.json()

                if pr_response.status_code == HTTP_CREATED:
                    pr_url = pr_data.get("html_url")
                    pr_number = pr_data.get("number")
                    await _add_label(
                        http_client,
                        repo_owner,
                        repo_name,
                        label_tok,
                        pr_number,
                    )
                    logger.info("PR created successfully: %s", pr_url)
                    return pr_url, pr_number, False

                if pr_response.status_code == HTTP_UNPROCESSABLE_ENTITY:
                    logger.error("GitHub API validation error (422): %s", pr_data.get("message"))
                    existing = await _find_existing_pr(
                        http_client=http_client,
                        repo_owner=repo_owner,
                        repo_name=repo_name,
                        github_token=token,
                        head_branch=head_branch,
                    )
                    pr_url, pr_number = existing
                    if pr_url:
                        logger.info("Using existing PR for head branch: %s", pr_url)
                        updated = await _update_github_pr(
                            http_client=http_client,
                            repo_owner=repo_owner,
                            repo_name=repo_name,
                            github_token=token,
                            pr_number=pr_number,
                            title=title,
                            body=body,
                        )
                        if not updated:
                            if token != tokens_to_try[-1]:
                                logger.info("Retrying existing PR update with installation token")
                                continue
                            return None, None, False
                        await _add_label(
                            http_client,
                            repo_owner,
                            repo_name,
                            label_tok,
                            pr_number,
                        )
                        return pr_url, pr_number, True
                    else:
                        logger.debug(
                            "Could not find existing PR with current token, will retry"
                            if token != tokens_to_try[-1]
                            else "Could not find existing PR"
                        )
                else:
                    logger.error(
                        "GitHub API error (%s): %s",
                        pr_response.status_code,
                        pr_data.get("message"),
                    )

                if "errors" in pr_data:
                    logger.error("GitHub API errors detail: %s", pr_data.get("errors"))

                # If this was the user token, fall through to retry with installation token
                if token != tokens_to_try[-1]:
                    logger.info("Retrying PR creation with installation token")
                    continue

                return None, None, False

            except httpx.HTTPError:
                logger.exception("Failed to create PR via GitHub API")
                if token != tokens_to_try[-1]:
                    logger.info("Retrying PR creation with installation token")
                    continue

                try:
                    existing_pr_url, existing_pr_number = await _find_existing_pr(
                        http_client=http_client,
                        repo_owner=repo_owner,
                        repo_name=repo_name,
                        github_token=token,
                        head_branch=head_branch,
                    )
                    if existing_pr_url:
                        await _add_label(
                            http_client,
                            repo_owner,
                            repo_name,
                            label_tok,
                            existing_pr_number,
                        )
                        logger.info("Found existing PR after HTTP error: %s", existing_pr_url)
                        return existing_pr_url, existing_pr_number, True
                except Exception:
                    logger.exception("Failed to find existing PR after HTTP error")

                return None, None, False

    return None, None, False


_OPENSWE_LABEL = "OpenSWE"


async def _add_label(
    http_client: httpx.AsyncClient,
    repo_owner: str,
    repo_name: str,
    github_token: str,
    pr_number: int | None,
) -> None:
    """Add the 'OpenSWE' label to a PR without failing PR creation on errors."""
    if not pr_number:
        return

    try:
        response = await http_client.post(
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/labels",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"labels": [_OPENSWE_LABEL]},
        )
        if response.is_success:
            logger.info("Added '%s' label to PR #%s", _OPENSWE_LABEL, pr_number)
        else:
            logger.warning(
                "Failed to add label to PR #%s (%s)",
                pr_number,
                response.status_code,
            )
    except httpx.HTTPError:
        logger.warning("Failed to add label to PR #%s", pr_number, exc_info=True)


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


async def _update_github_pr(
    http_client: httpx.AsyncClient,
    repo_owner: str,
    repo_name: str,
    github_token: str,
    pr_number: int | None,
    title: str,
    body: str,
) -> bool:
    """Update an existing PR's title and body via PATCH."""
    if pr_number is None:
        logger.warning("Cannot update PR: pr_number is None")
        return False
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = await http_client.patch(
            f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}",
            headers=headers,
            json={"title": title, "body": body},
        )
    except httpx.HTTPError:
        logger.warning("Failed to update PR #%s", pr_number, exc_info=True)
        return False
    if response.status_code == 200:  # noqa: PLR2004
        logger.info("Updated existing PR #%s with new title and body", pr_number)
        return True
    logger.warning(
        "Failed to update PR #%s (%s): %s",
        pr_number,
        response.status_code,
        response.json().get("message"),
    )
    return False


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
