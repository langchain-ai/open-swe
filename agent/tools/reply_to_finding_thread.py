from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_findings import get_finding, get_thread_id_from_runtime, update_finding_fields
from ..reviewer_publish import reply_to_review_comment
from ..utils.github_token import get_github_token


def reply_to_finding_thread(finding_id: str, body: str) -> dict[str, Any]:
    """Reply to the GitHub review thread for a tracked finding."""
    if not body.strip():
        return {"success": False, "error": "Reply body is required"}

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    repo_config = configurable.get("repo") if isinstance(configurable, dict) else None
    pr_number = configurable.get("pr_number") if isinstance(configurable, dict) else None
    if (
        not isinstance(repo_config, dict)
        or not repo_config.get("owner")
        or not repo_config.get("name")
        or not isinstance(pr_number, int)
    ):
        return {"success": False, "error": "Missing repo or PR info in run config"}

    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}

    return asyncio.run(
        _reply_to_finding_thread_async(
            finding_id=finding_id,
            body=body,
            owner=str(repo_config["owner"]),
            repo=str(repo_config["name"]),
            pr_number=pr_number,
            token=token,
        )
    )


async def _reply_to_finding_thread_async(
    *,
    finding_id: str,
    body: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> dict[str, Any]:
    thread_id = get_thread_id_from_runtime()
    finding = await get_finding(thread_id, finding_id)
    if finding is None:
        return {"success": False, "error": f"No finding found with id {finding_id}"}

    comment_id = finding.get("github_review_comment_id")
    if not isinstance(comment_id, int):
        return {"success": False, "error": "Finding has no GitHub review comment mapping"}

    response = await reply_to_review_comment(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        review_comment_id=comment_id,
        body=body.strip(),
        token=token,
    )
    if response is None:
        return {"success": False, "error": "GitHub did not accept the reply"}

    reply_id = response.get("id")
    updates: dict[str, Any] = {"last_reconciliation_note": "Replied to GitHub review thread."}
    if isinstance(reply_id, int):
        updates["last_review_reply_comment_id"] = reply_id
    updated = await update_finding_fields(thread_id, finding_id, updates)
    return {"success": True, "finding": updated, "reply_id": reply_id}
