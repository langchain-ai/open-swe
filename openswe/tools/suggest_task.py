"""Record future work without starting it."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import HttpUrl, TypeAdapter, ValidationError

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.repo_access import (
    require_repo_access_for_user,
    require_repo_access_for_workspace,
)
from openswe.database import session
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_names import normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.run_config import RunConfig
from openswe.slack.client import parse_github_pr_url
from openswe.tasks.store import TaskMembership
from openswe.tasks.suggestions import SuggestedTask, SuggestedTaskContent
from openswe.tools.access import direct_user_run
from openswe.users import User
from openswe.workspaces.routing import workspace_for_repo
from openswe.workspaces.store import WORKSPACES


@audit_tool(target_workspace="workspace")
async def suggest_task(
    summary: str,
    details: str,
    repo: str,
    workspace: str = "",
    context_links: list[str] | None = None,
) -> dict[str, object]:
    """Implement the `suggest_task` tool."""
    cfg = RunConfig.from_runtime()
    if not cfg.thread_id:
        return {"success": False, "error": "A suggestion needs an originating thread"}
    try:
        full_name = normalize_repo_full_name(repo)
        content = SuggestedTaskContent(summary=summary.strip(), details=details.strip())
        links = [str(TypeAdapter(HttpUrl).validate_python(link)) for link in context_links or ()]
    except (ValueError, ValidationError) as exc:
        return {"success": False, "error": str(exc)}
    # A shadow review is an experiment nobody on the pull request knows of.
    if cfg.review_shadow:
        return {"success": True, "dry_run": True, "suggested_task_id": None}
    owner, name = full_name.split("/", 1)
    target_workspace = workspace or await workspace_for_repo(owner, name) or cfg.workspace_slug
    if not target_workspace:
        return {"success": False, "error": "Specify the target workspace"}
    workspace_id = await WORKSPACES.id_for_slug(target_workspace)
    if workspace_id is None:
        return {"success": False, "error": "The target workspace no longer exists"}
    try:
        if (
            cfg.repo
            and f"{cfg.repo.owner}/{cfg.repo.name}".lower() == full_name.lower()
            and cfg.workspace_slug == target_workspace
            and (cfg.source == "github" or not cfg.github_login)
        ):
            await require_repo_access_for_workspace(full_name)
        elif cfg.github_login:
            await require_repo_access_for_user(cfg.github_login, full_name)
        else:
            return {
                "success": False,
                "error": "Cross-repository suggestions need a GitHub identity",
            }
    except HTTPException as exc:
        return {"success": False, "error": str(exc.detail)}
    user = (
        await User.get(UUID(cfg.user_id))
        if cfg.user_id
        else await User.for_identity("github", cfg.github_user_id)
        if cfg.github_user_id
        else await User.for_login("github", cfg.github_login)
        if cfg.github_login
        else await User.for_identity("slack", cfg.slack_thread.triggering_user_id)
        if cfg.slack_thread and cfg.slack_thread.triggering_user_id
        else None
    )
    task = await TaskMembership.context_for_thread(cfg.thread_id)
    pr_ref = parse_github_pr_url(cfg.pr_url) if cfg.pr_url else None
    if pr_ref is None and cfg.repo and cfg.pr_number is not None:
        pr_ref = parse_github_pr_url(
            f"https://github.com/{cfg.repo.owner}/{cfg.repo.name}/pull/{cfg.pr_number}"
        )
    pull_request = (
        await (await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)).ensure()
        if pr_ref
        else None
    )
    async with session() as orm:
        repository_id = await Repository.ensure_id(orm, full_name)
    actor_id = user.id if user and direct_user_run(cfg) else None
    suggestion = await SuggestedTask(
        summary=content.summary,
        details=content.details,
        workspace_id=workspace_id,
        repository_id=repository_id,
        thread_id=cfg.thread_id,
        task_id=task.task.id if task else None,
        pull_request_id=pull_request.id if pull_request else None,
        triggered_by=user.id if user else None,
        context_links=links,
        created_by=actor_id,
        updated_by=actor_id,
    ).create()
    return {"success": True, "suggested_task_id": str(suggestion.id)}
