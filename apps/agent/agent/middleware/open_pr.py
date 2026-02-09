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

logger = logging.getLogger(__name__)


def _extract_pr_params_from_messages(messages: list) -> dict[str, str] | None:
    """Extract PR title/body/commit_message from the last commit_and_open_pr tool result."""
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
                if isinstance(parsed, dict) and "title" in parsed:
                    return parsed
            except (ValueError, TypeError):
                pass
    return None


@after_agent
async def open_pr_if_needed(  # noqa: PLR0912, PLR0915
    state: AgentState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Middleware that commits/pushes changes and comments on Linear after agent runs."""
    from ..encryption import decrypt_token
    from ..server import (
        _SANDBOX_BACKENDS,
        comment_on_linear_issue,
        create_github_pr,
        get_github_default_branch,
    )

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

        pr_params = _extract_pr_params_from_messages(messages)

        if not pr_params:
            logger.info("No commit_and_open_pr tool call found, skipping PR creation")
            if linear_issue_id and last_message_content:
                comment = f""" **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        pr_title = pr_params.get("title", "feat: Open SWE PR")
        pr_body = pr_params.get("body", "Automated PR created by Open SWE agent.")
        commit_message = pr_params.get("commit_message", pr_title)

        if not thread_id:
            if linear_issue_id and last_message_content:
                comment = f"""🤖 **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        repo_config = configurable.get("repo", {})
        repo_owner = repo_config.get("owner")
        repo_name = repo_config.get("name")

        sandbox_backend = _SANDBOX_BACKENDS.get(thread_id)

        repo_dir = f"/workspace/{repo_name}"

        if not sandbox_backend or not repo_dir:
            if linear_issue_id and last_message_content:
                comment = f"""🤖 **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        result = await asyncio.to_thread(
            sandbox_backend.execute, f"cd {repo_dir} && git status --porcelain"
        )

        has_uncommitted_changes = result.exit_code == 0 and result.output.strip()

        await asyncio.to_thread(
            sandbox_backend.execute, f"cd {repo_dir} && git fetch origin 2>/dev/null || true"
        )
        git_log_cmd = (
            f"cd {repo_dir} && git log --oneline @{{upstream}}..HEAD 2>/dev/null "
            "|| git log --oneline origin/HEAD..HEAD 2>/dev/null || echo ''"
        )
        unpushed_result = await asyncio.to_thread(sandbox_backend.execute, git_log_cmd)
        has_unpushed_commits = unpushed_result.exit_code == 0 and unpushed_result.output.strip()

        has_changes = has_uncommitted_changes or has_unpushed_commits

        if not has_changes:
            logger.info("No changes detected, skipping PR creation")
            if linear_issue_id and last_message_content:
                comment = f"""🤖 **Agent Response**

{last_message_content}"""
                await comment_on_linear_issue(linear_issue_id, comment)
            return None

        logger.info("Changes detected, preparing PR for thread %s", thread_id)

        branch_result = await asyncio.to_thread(
            sandbox_backend.execute, f"cd {repo_dir} && git rev-parse --abbrev-ref HEAD"
        )
        current_branch = branch_result.output.strip() if branch_result.exit_code == 0 else ""

        target_branch = f"open-swe/{thread_id}"

        if current_branch != target_branch:
            checkout_result = await asyncio.to_thread(
                sandbox_backend.execute, f"cd {repo_dir} && git checkout -b {target_branch}"
            )
            if checkout_result.exit_code != 0:
                await asyncio.to_thread(
                    sandbox_backend.execute, f"cd {repo_dir} && git checkout {target_branch}"
                )

        await asyncio.to_thread(
            sandbox_backend.execute, f"cd {repo_dir} && git config user.name 'Open SWE[bot]'"
        )
        await asyncio.to_thread(
            sandbox_backend.execute,
            f"cd {repo_dir} && git config user.email 'Open SWE@users.noreply.github.com'",
        )

        await asyncio.to_thread(sandbox_backend.execute, f"cd {repo_dir} && git add -A")

        safe_commit_msg = commit_message.replace("'", "'\\''")
        await asyncio.to_thread(
            sandbox_backend.execute, f"cd {repo_dir} && git commit -m '{safe_commit_msg}'"
        )

        encrypted_token = configurable.get("github_token_encrypted")
        if encrypted_token:
            github_token = decrypt_token(encrypted_token)

        if github_token:
            remote_result = await asyncio.to_thread(
                sandbox_backend.execute, f"cd {repo_dir} && git remote get-url origin"
            )
            if remote_result.exit_code == 0:
                remote_url = remote_result.output.strip()
                if "github.com" in remote_url and "@" not in remote_url:
                    auth_url = remote_url.replace("https://", f"https://git:{github_token}@")
                    await asyncio.to_thread(
                        sandbox_backend.execute,
                        f"cd {repo_dir} && git push {auth_url} {target_branch}",
                    )
                else:
                    await asyncio.to_thread(
                        sandbox_backend.execute, f"cd {repo_dir} && git push origin {target_branch}"
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
                comment = f"""✅ **Pull Request Created**

I've created a pull request to address this issue:

**[PR #{pr_number}: {pr_title}]({pr_url})**

---

🤖 **Agent Response**

{last_message_content}"""
            else:
                comment = f"""🤖 **Agent Response**

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
                error_comment = f"""❌ **Agent Error**

An error occurred while processing this issue:

```
{type(e).__name__}: {e}
```"""
                await comment_on_linear_issue(linear_issue_id, error_comment)
        except Exception:
            logger.exception("Failed to post error comment to Linear")
    return None
