import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from ..encryption import decrypt_token
from ..utils.github import (
    create_github_pr,
    get_github_default_branch,
    git_add_all,
    git_checkout_branch,
    git_commit,
    git_config_user,
    git_current_branch,
    git_fetch_origin,
    git_has_uncommitted_changes,
    git_has_unpushed_commits,
    git_push,
)
from ..utils.sandbox_state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)


def commit_and_open_pr(
    title: str,
    body: str,
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Commit all current changes and open a GitHub Pull Request.

    You MUST call this tool when you have completed your work and want to
    submit your changes for review. This is the final step in your workflow.

    Before calling this tool, ensure you have:
    1. Reviewed your changes for correctness
    2. Run `make format` and `make lint` if a Makefile exists in the repo root

    ## Title Format (REQUIRED — keep under 70 characters)

    The PR title MUST follow this exact format:

        <type>: <short lowercase description> [closes <PROJECT_ID>-<ISSUE_NUMBER>]

    The description MUST be entirely lowercase (no capital letters).

    Where <type> is one of:
    - fix:   for bug fixes
    - feat:  for new features
    - chore: for maintenance tasks (deps, configs, cleanup)
    - ci:    for CI/CD changes

    The [closes ...] suffix links and auto-closes the Linear ticket.
    Use the linear_project_id and linear_issue_number from your context.

    Examples:
    - "fix: resolve null pointer in user auth [closes AA-123]"
    - "feat: add dark mode toggle to settings [closes ENG-456]"
    - "chore: upgrade dependencies to latest versions [closes OPS-789]"

    ## Body Format (REQUIRED)

    The PR body MUST follow this exact template:

        ## Description
        <Explain WHY this PR is needed. Include:
        - List of changes made
        - Reference to the Linear issue or design docs
        - Any context on the approach taken>

        ## Test Plan
        - [ ] <specific test step 1>
        - [ ] <specific test step 2>

    Example body:

        ## Description
        Fixes the null pointer exception that occurs when a user without
        a profile attempts to authenticate. The root cause was a missing
        null check in the `getProfile` method.

        Changes:
        - Added null check in `auth/getProfile.ts`
        - Added fallback default profile object
        - Updated related unit tests

        Resolves AA-123

        ## Test Plan
        - [ ] Verify login works for users without profiles
        - [ ] Verify existing users are unaffected
        - [ ] Run `yarn test` and confirm all tests pass

    ## Commit Message

    The commit message should be concise (1-2 sentences) and focus on the "why"
    rather than the "what". Summarize the nature of the changes: new feature,
    bug fix, refactoring, etc. If not provided, the PR title is used.

    Args:
        title: PR title following the format above (e.g. "fix: resolve auth bug [closes AA-123]")
        body: PR description following the template above with ## Description and ## Test Plan
        commit_message: Optional git commit message. If not provided, the PR title is used.

    Returns:
        Dictionary containing:
        - success: Whether the operation completed successfully
        - error: Error string if something failed, otherwise None
        - pr_url: URL of the created PR if successful, otherwise None
    """
    try:
        config = get_config()
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        if not thread_id:
            return {"success": False, "error": "Missing thread_id in config", "pr_url": None}

        repo_config = configurable.get("repo", {})
        repo_owner = repo_config.get("owner")
        repo_name = repo_config.get("name")
        if not repo_owner or not repo_name:
            return {
                "success": False,
                "error": "Missing repo owner/name in config",
                "pr_url": None,
            }

        sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
        if not sandbox_backend:
            sandbox_id = configurable.get("sandbox_id")

            if not sandbox_id:
                return {"success": False, "error": "No sandbox found for thread", "pr_url": None}

            # Import here to avoid circular import with server.py
            from ..server import _create_langsmith_sandbox

            sandbox_backend = _create_langsmith_sandbox(sandbox_id)
            SANDBOX_BACKENDS[thread_id] = sandbox_backend

        repo_dir = f"/workspace/{repo_name}"

        has_uncommitted_changes = git_has_uncommitted_changes(sandbox_backend, repo_dir)
        git_fetch_origin(sandbox_backend, repo_dir)
        has_unpushed_commits = git_has_unpushed_commits(sandbox_backend, repo_dir)

        if not (has_uncommitted_changes or has_unpushed_commits):
            return {"success": False, "error": "No changes detected", "pr_url": None}

        current_branch = git_current_branch(sandbox_backend, repo_dir)
        target_branch = f"open-swe/{thread_id}"
        if current_branch != target_branch:
            if not git_checkout_branch(sandbox_backend, repo_dir, target_branch):
                return {
                    "success": False,
                    "error": f"Failed to checkout branch {target_branch}",
                    "pr_url": None,
                }

        git_config_user(
            sandbox_backend,
            repo_dir,
            "Open SWE[bot]",
            "Open SWE@users.noreply.github.com",
        )
        git_add_all(sandbox_backend, repo_dir)

        commit_msg = commit_message or title
        if has_uncommitted_changes:
            commit_result = git_commit(sandbox_backend, repo_dir, commit_msg)
            if commit_result.exit_code != 0:
                return {
                    "success": False,
                    "error": f"Git commit failed: {commit_result.output.strip()}",
                    "pr_url": None,
                }

        encrypted_token = configurable.get("github_token_encrypted")
        github_token = decrypt_token(encrypted_token) if encrypted_token else None
        if not github_token:
            return {"success": False, "error": "Missing GitHub token", "pr_url": None}

        push_result = git_push(sandbox_backend, repo_dir, target_branch, github_token)
        if push_result.exit_code != 0:
            return {
                "success": False,
                "error": f"Git push failed: {push_result.output.strip()}",
                "pr_url": None,
            }

        base_branch = asyncio.run(
            get_github_default_branch(repo_owner, repo_name, github_token)
        )
        pr_url, _pr_number = asyncio.run(
            create_github_pr(
                repo_owner=repo_owner,
                repo_name=repo_name,
                github_token=github_token,
                title=title,
                head_branch=target_branch,
                base_branch=base_branch,
                body=body,
            )
        )

        if not pr_url:
            return {
                "success": False,
                "error": "Failed to create GitHub PR",
                "pr_url": None,
            }

        return {"success": True, "error": None, "pr_url": pr_url}
    except Exception as e:
        logger.exception("commit_and_open_pr failed")
        return {"success": False, "error": f"{type(e).__name__}: {e}", "pr_url": None}
