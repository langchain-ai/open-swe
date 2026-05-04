"""After-agent middleware that creates a GitHub PR if needed."""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    add_pr_collaboration_note,
    add_user_coauthor_trailer,
    resolve_triggering_user_identity,
)
from ..utils.github import (
    git_add_all,
    git_checkout_branch,
    git_checkout_existing_branch,
    git_commit,
    git_config_user,
    git_current_branch,
    git_fetch_origin,
    git_has_uncommitted_changes,
    git_has_unpushed_commits,
    git_push,
)
from ..utils.github_token import get_github_token
from ..utils.sandbox_paths import aresolve_repo_dir
from ..utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)


def _run_gh(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    command: str,
) -> ExecuteResponse:
    safe_repo_dir = shlex.quote(repo_dir)
    return sandbox_backend.execute(f"cd {safe_repo_dir} && GH_TOKEN=dummy gh {command}")


def _default_branch(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
) -> str:
    result = _run_gh(
        sandbox_backend,
        repo_dir,
        "repo view --json defaultBranchRef --jq .defaultBranchRef.name",
    )
    return result.output.strip() if result.exit_code == 0 else "main"


def _existing_pr_url(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
) -> str | None:
    result = _run_gh(sandbox_backend, repo_dir, "pr view --json url --jq .url")
    if result.exit_code != 0:
        return None
    pr_url = result.output.strip()
    return pr_url or None


def _create_or_update_pr(
    sandbox_backend: SandboxBackendProtocol,
    repo_dir: str,
    title: str,
    body: str,
    base_branch: str,
    target_branch: str,
) -> str | None:
    safe_title = shlex.quote(title)
    safe_body = shlex.quote(body)

    existing_url = _existing_pr_url(sandbox_backend, repo_dir)
    if existing_url:
        result = _run_gh(
            sandbox_backend,
            repo_dir,
            f"pr edit --title {safe_title} --body {safe_body}",
        )
        if result.exit_code != 0:
            logger.warning("Failed to update existing PR: %s", result.output.strip())
        return existing_url

    safe_base = shlex.quote(base_branch)
    safe_head = shlex.quote(target_branch)
    result = _run_gh(
        sandbox_backend,
        repo_dir,
        f"pr create --draft --title {safe_title} --body {safe_body} --base {safe_base} --head {safe_head}",
    )
    if result.exit_code != 0:
        logger.warning("Failed to create PR via gh: %s", result.output.strip())
        return None
    pr_url = result.output.strip().splitlines()[-1] if result.output.strip() else None
    return pr_url


@after_agent
async def open_pr_if_needed(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Commit/push changes and ensure a draft PR exists after agent runs."""
    logger.info("After-agent middleware started")

    try:
        config = get_config()
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        logger.debug("Middleware running for thread %s", thread_id)

        github_token = get_github_token(config)
        user_identity = await asyncio.to_thread(
            resolve_triggering_user_identity, config, github_token
        )
        pr_title = "feat: open swe changes"
        pr_body = add_pr_collaboration_note(
            "Automated draft PR created by Open SWE.",
            user_identity,
        )
        commit_message = add_user_coauthor_trailer(pr_title, user_identity)

        if not thread_id:
            raise ValueError("No thread_id found in config")

        repo_config = configurable.get("repo", {})
        repo_name = repo_config.get("name")

        sandbox_backend = await get_sandbox_backend(thread_id)
        if not sandbox_backend or not repo_name:
            return None
        repo_dir = await aresolve_repo_dir(sandbox_backend, repo_name)

        has_uncommitted_changes = await asyncio.to_thread(
            git_has_uncommitted_changes, sandbox_backend, repo_dir
        )

        await asyncio.to_thread(git_fetch_origin, sandbox_backend, repo_dir)
        has_unpushed_commits = await asyncio.to_thread(
            git_has_unpushed_commits, sandbox_backend, repo_dir
        )

        has_changes = has_uncommitted_changes or has_unpushed_commits

        if not has_changes:
            logger.info("No changes detected, skipping PR creation")
            return None

        logger.info("Changes detected, preparing PR for thread %s", thread_id)

        metadata = config.get("metadata", {})
        branch_name = metadata.get("branch_name")
        current_branch = await asyncio.to_thread(git_current_branch, sandbox_backend, repo_dir)
        target_branch = branch_name if branch_name else f"open-swe/{thread_id}"

        if current_branch != target_branch:
            if branch_name:
                await asyncio.to_thread(
                    git_checkout_existing_branch, sandbox_backend, repo_dir, target_branch
                )
            else:
                await asyncio.to_thread(
                    git_checkout_branch, sandbox_backend, repo_dir, target_branch
                )

        await asyncio.to_thread(
            git_config_user,
            sandbox_backend,
            repo_dir,
            OPEN_SWE_BOT_NAME,
            OPEN_SWE_BOT_EMAIL,
        )
        await asyncio.to_thread(git_add_all, sandbox_backend, repo_dir)
        if has_uncommitted_changes:
            await asyncio.to_thread(git_commit, sandbox_backend, repo_dir, commit_message)

        await asyncio.to_thread(git_push, sandbox_backend, repo_dir, target_branch)

        base_branch = await asyncio.to_thread(_default_branch, sandbox_backend, repo_dir)
        logger.info("Using base branch: %s", base_branch)

        pr_url = await asyncio.to_thread(
            _create_or_update_pr,
            sandbox_backend,
            repo_dir,
            pr_title,
            pr_body,
            base_branch,
            target_branch,
        )
        if pr_url:
            logger.info("Ensured draft PR exists: %s", pr_url)

        logger.info("After-agent middleware completed successfully")

    except Exception:
        logger.exception("Error in after-agent middleware")
    return None
