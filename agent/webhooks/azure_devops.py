"""Azure DevOps Service Hook handlers (work item + PR comment)."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from langchain_core.messages.content import create_text_block

from agent import webapp
from agent.utils.azure_devops_payload import (
    AZURE_DEVOPS_PR_COMMENT_EVENT_TYPES,
    extract_pull_request_context_from_comment_payload,
    extract_trigger_comment_text,
    extract_work_item_description_from_payload,
    extract_work_item_id_from_payload,
    extract_work_item_title_from_payload,
    parse_org_project_from_pr_comment_payload,
    parse_org_project_from_service_hook,
)

logger = logging.getLogger(__name__)


def generate_thread_id_from_azure_devops_work_item(
    organization: str,
    project: str,
    work_item_id: int,
) -> str:
    key = f"ado-wi:{organization.strip().lower()}:{project.strip().lower()}:{work_item_id}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return (
        f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-"
        f"{digest[16:20]}-{digest[20:32]}"
    )


def generate_thread_id_from_azure_devops_pr(
    organization: str,
    project: str,
    repository_id: str,
    pull_request_id: int,
) -> str:
    key = (
        f"ado-pr:{organization.strip().lower()}:"
        f"{project.strip().lower()}:{repository_id.strip().lower()}:{pull_request_id}"
    )
    digest = hashlib.sha256(key.encode()).hexdigest()
    return (
        f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-"
        f"{digest[16:20]}-{digest[20:32]}"
    )


def build_azure_devops_repo_config(organization: str, project: str) -> dict[str, str] | None:
    """Map webhook org/project to a single repo via ``AZURE_DEVOPS_REPO`` env (MVP)."""
    repo_name = (os.environ.get("AZURE_DEVOPS_REPO") or "").strip()
    if not repo_name:
        logger.warning(
            "AZURE_DEVOPS_REPO is not set; cannot route Azure DevOps webhook for %s/%s",
            organization,
            project,
        )
        return None
    return {
        "scm_provider": "azure_devops",
        "owner": organization,
        "project": project,
        "name": repo_name,
    }


async def process_azure_devops_work_item(
    organization: str,
    project: str,
    work_item_id: int,
    repo_config: dict[str, str],
    payload: dict[str, Any],
) -> None:
    title = extract_work_item_title_from_payload(payload) or f"Work item {work_item_id}"
    description = extract_work_item_description_from_payload(payload) or "No description"
    trigger_text = extract_trigger_comment_text(payload)
    prompt = (
        "Please work on the following Azure DevOps work item:\n\n"
        f"## Title: {title}\n\n"
        f"## Work item ID: {work_item_id}\n\n"
        f"## Description:\n{description}\n\n"
        f"## Trigger comment:\n{trigger_text}\n\n"
        "When finished, push your branch and call `open_pull_request` with the work item title."
    )
    thread_id = generate_thread_id_from_azure_devops_work_item(organization, project, work_item_id)
    configurable: dict[str, Any] = {
        "repo": repo_config,
        "source": "azure_devops",
        "user_email": "",
        "azure_devops_work_item": {
            "id": work_item_id,
            "organization": organization,
            "project": project,
        },
    }
    await webapp.dispatch_agent_run(
        thread_id,
        [create_text_block(prompt)],
        configurable,
        source="azure_devops",
        metadata=webapp._AGENT_VERSION_METADATA,
    )


async def process_azure_devops_pull_request_comment(
    payload: dict[str, Any],
    repo_config: dict[str, str],
    organization: str,
    project: str,
) -> None:
    pr_ctx = extract_pull_request_context_from_comment_payload(payload)
    repository_id = (
        (payload.get("resource") or {}).get("pullRequest", {}).get("repository", {}).get("id")
    )
    pull_request_id = (payload.get("resource") or {}).get("pullRequest", {}).get("pullRequestId")
    if not repository_id or pull_request_id is None:
        logger.warning("Azure DevOps PR webhook missing repository or pull request id")
        return

    title = (pr_ctx.get("title") or "Untitled").strip()
    description = (pr_ctx.get("description") or "").strip()
    trigger_body = (pr_ctx.get("trigger_comment_text") or "").strip()
    source_branch = (pr_ctx.get("source_branch_short_name") or "").strip()
    pr_url = (pr_ctx.get("pr_web_url") or "").strip()

    prompt = (
        "Please work on this Azure DevOps pull request follow-up:\n\n"
        f"## Pull request: {title}\n\n"
        f"**PR URL:** {pr_url or '(unknown)'}\n\n"
        f"**Source branch:** `{source_branch or 'unknown'}`\n\n"
        f"## Description:\n{description}\n\n"
        f"## Trigger comment:\n{trigger_body}\n\n"
        "The sandbox is on the PR source branch. Push to the same branch and use "
        "`open_pull_request` only if no PR exists yet."
    )

    thread_id = generate_thread_id_from_azure_devops_pr(
        organization,
        project,
        str(repository_id),
        int(pull_request_id),
    )
    configurable: dict[str, Any] = {
        "repo": repo_config,
        "source": "azure_devops",
        "user_email": "",
        "azure_devops_pull_request": {
            "organization": organization,
            "project": project,
            "repository_id": str(repository_id),
            "pull_request_id": int(pull_request_id),
            "source_branch_short_name": source_branch,
            "pr_web_url": pr_url,
            "title": title,
        },
    }
    await webapp.dispatch_agent_run(
        thread_id,
        [create_text_block(prompt)],
        configurable,
        source="azure_devops",
        metadata=webapp._AGENT_VERSION_METADATA,
    )


async def handle_azure_devops_webhook_payload(payload: dict[str, Any]) -> dict[str, str]:
    event_type = (payload.get("eventType") or "").strip()
    if event_type in AZURE_DEVOPS_PR_COMMENT_EVENT_TYPES:
        organization, project = parse_org_project_from_pr_comment_payload(payload)
    else:
        organization, project = parse_org_project_from_service_hook(payload)

    if not organization or not project:
        return {"status": "ignored", "reason": "Missing organization or project in payload"}

    repo_config = build_azure_devops_repo_config(organization, project)
    if repo_config is None:
        return {"status": "ignored", "reason": "AZURE_DEVOPS_REPO not configured"}

    if event_type in AZURE_DEVOPS_PR_COMMENT_EVENT_TYPES:
        await process_azure_devops_pull_request_comment(
            payload, repo_config, organization, project
        )
        return {"status": "accepted", "message": "Processing Azure DevOps PR comment"}

    work_item_id = extract_work_item_id_from_payload(payload)
    if work_item_id is None:
        return {"status": "ignored", "reason": "Missing work item id in payload"}

    await process_azure_devops_work_item(
        organization, project, work_item_id, repo_config, payload
    )
    return {"status": "accepted", "message": "Processing Azure DevOps work item"}
