"""Admin-controlled GitHub workflow dispatch."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient
from openswe.github.workflows import WorkflowPolicies, WorkflowRule, workflow_identity
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("repository", "workflow"))


@audit_tool(skip_read=True)
@access(_WRITE, per_call=lambda args: _READ if args.get("action") == "read" else _WRITE)
async def manage_workflow_allowlist(
    action: Literal["read", "set", "remove"],
    repository: str,
    workflow: str,
    ref: str | None = None,
    fixed_inputs: dict[str, str] | None = None,
    allow_bot_inputs: bool = False,
    workspace: str | None = None,
) -> dict[str, object]:
    """Configure one workflow's fixed ref and permitted inputs as an admin."""
    repository, _ = workflow_identity(repository, workflow)
    login = configurable().github_login
    if not login:
        raise ValueError("An authenticated requester is required")
    await require_repo_access_for_user(login, repository)
    policies = WorkflowPolicies(
        workspace or configurable().workspace_slug or DEFAULT_WORKSPACE_SLUG
    )
    if action == "set":
        rule = WorkflowRule(
            ref=ref or "", fixed_inputs=fixed_inputs or {}, allow_bot_inputs=allow_bot_inputs
        )
        await policies.set(repository, workflow, rule)
    elif action in {"read", "remove"}:
        if ref is not None or fixed_inputs is not None or allow_bot_inputs:
            raise ValueError("Configuration fields are only accepted for set")
        if action == "remove":
            await policies.remove(repository, workflow)
    else:
        raise ValueError("Action must be read, set, or remove")
    rule = await policies.read(repository, workflow)
    return {
        "repository": repository,
        "workflow": workflow,
        "workspace": policies.workspace,
        "rule": rule.model_dump() if rule else None,
    }


@audit_tool()
@access(Policy(trusted="anywhere"))
async def trigger_github_workflow(
    repository: str,
    workflow: str,
    inputs: dict[str, str] | None = None,
) -> dict[str, object]:
    """Dispatch an allowlisted workflow at its admin-fixed ref."""
    repository, _ = workflow_identity(repository, workflow)
    login = configurable().github_login
    if not login:
        raise ValueError("An authenticated requester is required")
    policies = WorkflowPolicies(configurable().workspace_slug or DEFAULT_WORKSPACE_SLUG)
    rule = await policies.read(repository, workflow)
    if not rule:
        raise ValueError("This workflow is not allowlisted")
    resolved_inputs = rule.dispatch_inputs(inputs)
    owner, name = repository.split("/")
    async with GitHubClient.as_app(owner, name, permissions={"metadata": "read"}) as github:
        if not await github.repo(owner, name).can_write(login):
            raise ValueError("The requester must have write access to this repository")
    async with GitHubClient.as_app(owner, name, permissions={"actions": "write"}) as github:
        await github.repo(owner, name).dispatch_workflow(workflow, rule.ref, resolved_inputs)
    return {
        "status": "requested",
        "repository": repository,
        "workflow": workflow,
        "ref": rule.ref,
        "url": f"https://github.com/{repository}/actions/workflows/{workflow}",
    }
