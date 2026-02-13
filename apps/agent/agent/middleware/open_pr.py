"""After-agent middleware that creates a GitHub PR and comments on Linear.

Runs once after the agent finishes.  If the agent called the
``commit_and_open_pr`` tool, this middleware commits any remaining changes,
pushes to a feature branch, opens a GitHub PR, and posts a summary comment
back to the originating Linear issue.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

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
from ..utils.linear import comment_on_linear_issue
from ..utils.sandbox_state import SANDBOX_BACKENDS

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
    """Middleware that commits/pushes changes and comments on Linear after agent runs."""
    logger.info("After-agent middleware started")
    pr_url = None
    pr_number = None

    try:
        config = get_config()
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        logger.debug("Middleware running for thread %s", thread_id)

        last_message_content = ""
        messages = state.get("messages", [])
        if messages:
            last_message = messages[-1]
            if isinstance(last_message, dict):
                last_message_content = last_message.get("content", "")
            elif hasattr(last_message, "content"):
                last_message_content = last_message.content

        linear_issue = configurable.get("linear_issue", {})
        linear_issue_id = linear_issue.get("id")

        pr_payload = _extract_pr_params_from_messages(messages)

        if not pr_payload:
            logger.info("No commit_and_open_pr tool call found, skipping PR creation")
            if linear_issue_id and last_message_content:
                comment = f""" **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        if "success" in pr_payload:
            pr_url = pr_payload.get("pr_url")
            error = pr_payload.get("error")
            if linear_issue_id and last_message_content:
                if pr_url:
                    comment = f"""**Pull Request Created**

I've created a pull request to address this issue:

{pr_url}

---
 **Agent Response**

{last_message_content}"""
                elif error:
                    comment = f"""**Pull Request Error**

{error}

---

**Agent Response**

{last_message_content}"""
                else:
                    comment = f""" **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        pr_title = pr_payload.get("title", "feat: Open SWE PR")
        pr_body = pr_payload.get("body", "Automated PR created by Open SWE agent.")
        commit_message = pr_payload.get("commit_message", pr_title)

        if not thread_id:
            if linear_issue_id and last_message_content:
                comment = f"""🤖 **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        repo_config = configurable.get("repo", {})
        repo_owner = repo_config.get("owner")
        repo_name = repo_config.get("name")

        sandbox_backend = SANDBOX_BACKENDS.get(thread_id)

        repo_dir = f"/workspace/{repo_name}"

        if not sandbox_backend or not repo_dir:
            if linear_issue_id and last_message_content:
                comment = f"""🤖 **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

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
            if linear_issue_id and last_message_content:
                comment = f"""🤖 **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        logger.info("Changes detected, preparing PR for thread %s", thread_id)

        current_branch = await asyncio.to_thread(
            git_current_branch, sandbox_backend, repo_dir
        )

        target_branch = f"open-swe/{thread_id}"

        if current_branch != target_branch:
            await asyncio.to_thread(
                git_checkout_branch, sandbox_backend, repo_dir, target_branch
            )

        await asyncio.to_thread(
            git_config_user,
            sandbox_backend,
            repo_dir,
            "Open SWE[bot]",
            "Open SWE@users.noreply.github.com",
        )
        await asyncio.to_thread(git_add_all, sandbox_backend, repo_dir)
        await asyncio.to_thread(git_commit, sandbox_backend, repo_dir, commit_message)

        encrypted_token = configurable.get("github_token_encrypted")
        github_token = None
        if encrypted_token:
            github_token = decrypt_token(encrypted_token)

        if github_token:
            await asyncio.to_thread(
                git_push, sandbox_backend, repo_dir, target_branch, github_token
            )

            base_branch = await get_github_default_branch(repo_owner, repo_name, github_token)
            logger.info("Using base branch: %s", base_branch)

            pr_url, pr_number = await create_github_pr(
                repo_owner=repo_owner,
                repo_name=repo_name,
                github_token=github_token,
                title=pr_title,
                head_branch=target_branch,
                base_branch=base_branch,
                body=pr_body,
            )

            linear_issue = configurable.get("linear_issue", {})
            linear_issue_id = linear_issue.get("id")

        if linear_issue_id and last_message_content:
            if pr_url:
                comment = f"""**Pull Request Created**

I've created a pull request to address this issue:

**[PR #{pr_number}: {pr_title}]({pr_url})**

---

🤖 **Agent Response**

{last_message_content}"""
            else:
                comment = f""" **Agent Response**

{last_message_content}"""
            await comment_on_linear_issue(linear_issue_id, comment)

        logger.info("After-agent middleware completed successfully")

    except Exception as e:
        logger.exception("Error in after-agent middleware")
        try:
            config = get_config()
            configurable = config.get("configurable", {})
            linear_issue = configurable.get("linear_issue", {})
            linear_issue_id = linear_issue.get("id")
            if linear_issue_id:
                error_comment = f""" **Agent Error**

An error occurred while processing this issue:

```
{type(e).__name__}: {e}
```"""
                await comment_on_linear_issue(linear_issue_id, error_comment)
        except Exception:
            logger.exception("Failed to post error comment to Linear")
    return None
