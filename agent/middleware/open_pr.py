"""After-agent middleware that creates a GitHub PR if needed.

Runs once after the agent finishes as a safety net. If the agent called
``commit_and_open_pr`` and it already succeeded, this is a no-op. Otherwise it
commits any remaining changes, pushes to a feature branch, and opens a GitHub PR.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any

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
    create_github_pr,
    get_github_default_branch,
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
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_token import get_github_token
from ..utils.sandbox_paths import aresolve_repo_dir
from ..utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)


def _extract_pr_params_from_messages(messages: list) -> dict[str, Any] | None:
    """Extract commit_and_open_pr tool result payload."""
    for msg in reversed(messages):
        if isinstance(msg, dict):
            content = msg.get("content", "")
            name = msg.get("name", "")
        else:
            content = getattr(msg, "content", "")
            name = getattr(msg, "name", "")

        if name == "commit_and_open_pr" and content:
            try:
                parsed = _json.loads(content) if isinstance(content, str) else content
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass
    return None


@after_agent
async def open_pr_if_needed(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Middleware that commits/pushes changes after agent runs if `commit_and_open_pr` tool didn't."""
    logger.info("After-agent middleware started")

    try:
        config = get_config()
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        logger.debug("Middleware running for thread %s", thread_id)

        messages = state.get("messages", [])
        pr_payload = _extract_pr_params_from_messages(messages)

        if not pr_payload:
            logger.info("No commit_and_open_pr tool call found, skipping PR creation")
            return None

        if "success" in pr_payload:
            # Tool already handled commit/push/PR creation
            return None

        pr_title = pr_payload.get("title", "feat: Open SWE PR")
        pr_body = pr_payload.get("body", "Automated PR created by Open SWE agent.")
        commit_message = pr_payload.get("commit_message", pr_title)
        github_token = get_github_token()
        user_identity = await asyncio.to_thread(
            resolve_triggering_user_identity, config, github_token
        )
        pr_body = add_pr_collaboration_note(pr_body, user_identity)
        commit_message = add_user_coauthor_trailer(commit_message, user_identity)

        if not thread_id:
            raise ValueError("No thread_id found in config")

        repo_config = configurable.get("repo", {})
        repo_owner = repo_config.get("owner")
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
                # Existing branch — plain checkout, do not create or reset
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
        await asyncio.to_thread(git_commit, sandbox_backend, repo_dir, commit_message)

        installation_token = await get_github_app_installation_token()
        if not installation_token:
            logger.error("Failed to get GitHub App installation token for thread %s", thread_id)
            return None

        await asyncio.to_thread(git_push, sandbox_backend, repo_dir, target_branch)

        base_branch = await get_github_default_branch(repo_owner, repo_name, installation_token)
        logger.info("Using base branch: %s", base_branch)

        await create_github_pr(
            repo_owner=repo_owner,
            repo_name=repo_name,
            github_token=installation_token,
            title=pr_title,
            head_branch=target_branch,
            base_branch=base_branch,
            body=pr_body,
        )

        logger.info("After-agent middleware completed successfully")

    except Exception:
        logger.exception("Error in after-agent middleware")
    return None
