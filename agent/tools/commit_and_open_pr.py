import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from ..utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    add_pr_collaboration_note,
    add_user_coauthor_trailer,
    resolve_triggering_user_identity,
)
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
from ..utils.github_token import get_github_token
from ..utils.sandbox_paths import resolve_repo_dir
from ..utils.sandbox_state import get_sandbox_backend_sync

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
        <1-3 sentences explaining WHY this PR is needed and the approach taken.
        DO NOT list files changed or enumerate code
        changes — that information is already in the commit history.>

        ## Test Plan
        - [ ] <new test case or manual verification step ONLY for new behavior>

    IMPORTANT RULES for the body:
    - NEVER add a "Changes:" or "Files changed:" section — it's redundant with git commits
    - Test Plan must ONLY include new/novel verification steps, NOT "run existing tests"
      or "verify existing functionality is unaffected" — those are always implied
      If it's a UI change you may say something along the lines of "Test in preview deployment"
    - Keep the entire body concise (aim for under 10 lines total)

    Example body:

        ## Description
        Fixes the null pointer exception when a user without a profile authenticates.
        The root cause was a missing null check in `getProfile`.

        Resolves AA-123

        ## Test Plan
        - [ ] Verify login works for users without profiles

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
        - pr_existing: Whether a PR already existed for this branch
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

        sandbox_backend = get_sandbox_backend_sync(thread_id)
        if not sandbox_backend:
            return {"success": False, "error": "No sandbox found for thread", "pr_url": None}

        repo_dir = resolve_repo_dir(sandbox_backend, repo_name)
        github_token = get_github_token()
        user_identity = resolve_triggering_user_identity(config, github_token)
        pr_body = add_pr_collaboration_note(body, user_identity)

        has_uncommitted_changes = git_has_uncommitted_changes(sandbox_backend, repo_dir)
        git_fetch_origin(sandbox_backend, repo_dir)
        has_unpushed_commits = git_has_unpushed_commits(sandbox_backend, repo_dir)

        if not (has_uncommitted_changes or has_unpushed_commits):
            return {"success": False, "error": "No changes detected", "pr_url": None}

        metadata = config.get("metadata", {})
        branch_name = metadata.get("branch_name")
        current_branch = git_current_branch(sandbox_backend, repo_dir)
        target_branch = branch_name if branch_name else f"open-swe/{thread_id}"
        if current_branch != target_branch:
            if branch_name:
                # Existing branch — plain checkout, do not create or reset
                result = sandbox_backend.execute(f"cd {repo_dir} && git checkout {target_branch}")
                if result.exit_code != 0:
                    return {
                        "success": False,
                        "error": f"Failed to checkout branch {target_branch}",
                        "pr_url": None,
                    }
            elif not git_checkout_branch(sandbox_backend, repo_dir, target_branch):
                return {
                    "success": False,
                    "error": f"Failed to checkout branch {target_branch}",
                    "pr_url": None,
                }

        git_config_user(
            sandbox_backend,
            repo_dir,
            OPEN_SWE_BOT_NAME,
            OPEN_SWE_BOT_EMAIL,
        )
        git_add_all(sandbox_backend, repo_dir)

        commit_msg = add_user_coauthor_trailer(commit_message or title, user_identity)
        if has_uncommitted_changes:
            commit_result = git_commit(sandbox_backend, repo_dir, commit_msg)
            if commit_result.exit_code != 0:
                return {
                    "success": False,
                    "error": f"Git commit failed: {commit_result.output.strip()}",
                    "pr_url": None,
                }

        if not github_token:
            logger.error("commit_and_open_pr missing GitHub token for thread %s", thread_id)
            return {
                "success": False,
                "error": "Missing GitHub token",
                "pr_url": None,
            }

        push_result = git_push(sandbox_backend, repo_dir, target_branch, github_token)
        if push_result.exit_code != 0:
            return {
                "success": False,
                "error": f"Git push failed: {push_result.output.strip()}",
                "pr_url": None,
            }

        base_branch = asyncio.run(get_github_default_branch(repo_owner, repo_name, github_token))
        pr_url, _pr_number, pr_existing = asyncio.run(
            create_github_pr(
                repo_owner=repo_owner,
                repo_name=repo_name,
                github_token=github_token,
                title=title,
                head_branch=target_branch,
                base_branch=base_branch,
                body=pr_body,
            )
        )

        if not pr_url:
            return {
                "success": False,
                "error": "Failed to create GitHub PR",
                "pr_url": None,
                "pr_existing": False,
            }

        return {
            "success": True,
            "error": None,
            "pr_url": pr_url,
            "pr_existing": pr_existing,
        }
    except Exception as e:
        logger.exception("commit_and_open_pr failed")
        return {"success": False, "error": f"{type(e).__name__}: {e}", "pr_url": None}
