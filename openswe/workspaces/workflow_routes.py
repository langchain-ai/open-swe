"""Admin dashboard workflow policies."""

from fastapi import APIRouter, HTTPException, Request

from openswe.audit_logs.context import bind_workspace
from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import ADMIN_DEP
from openswe.github.http import GitHubClient, WorkflowOptions
from openswe.github.workflows import WorkflowPolicies, WorkflowPolicy, WorkflowRule
from openswe.workspaces.store import slugify

router = APIRouter(tags=["workspaces"])


@router.get("/workspaces/{slug}/workflow-options/{owner}/{name}")
async def workflow_options(
    slug: str, owner: str, name: str, admin: dict[str, object] = ADMIN_DEP
) -> WorkflowOptions:
    await WorkflowPolicies(slugify(slug)).require_repository(f"{owner}/{name}")
    login = admin.get("sub")
    if not isinstance(login, str) or not login:
        raise HTTPException(401, "Authenticated requester required")
    async with GitHubClient.as_user(login) as github:
        return await github.repo(owner, name).workflow_options()


@router.get("/workspaces/{slug}/workflow-policies")
async def list_workflow_policies(
    slug: str, admin: dict[str, object] = ADMIN_DEP
) -> list[WorkflowPolicy]:
    try:
        return await WorkflowPolicies(slugify(slug)).list_policies()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/workspaces/{slug}/workflow-policies/{owner}/{name}/{workflow}")
@audit_endpoint
async def set_workflow_policy(
    slug: str,
    owner: str,
    name: str,
    workflow: str,
    body: WorkflowRule,
    request: Request,
    admin: dict[str, object] = ADMIN_DEP,
) -> WorkflowPolicy:
    await bind_workspace(request, slug)
    try:
        return await WorkflowPolicies(slugify(slug)).set(f"{owner}/{name}", workflow, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/workspaces/{slug}/workflow-policies/{owner}/{name}/{workflow}", status_code=204)
@audit_endpoint
async def remove_workflow_policy(
    slug: str,
    owner: str,
    name: str,
    workflow: str,
    request: Request,
    admin: dict[str, object] = ADMIN_DEP,
) -> None:
    await bind_workspace(request, slug)
    try:
        await WorkflowPolicies(slugify(slug)).remove(f"{owner}/{name}", workflow)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
