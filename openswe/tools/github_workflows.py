"""Admin-controlled GitHub workflow dispatch."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.github.http import GitHubClient
from openswe.review.styles import normalize_repo_full_name
from openswe.store import get_value, put_value
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable

_NAMESPACE = ["github_workflow_allowlist"]
_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("repository", "workflow"))


class WorkflowRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ref: str = Field(min_length=1)
    fixed_inputs: dict[str, str] = Field(default_factory=dict)
    allow_bot_inputs: bool = False

    def dispatch_inputs(self, supplied: dict[str, str] | None) -> dict[str, str]:
        supplied = supplied or {}
        if supplied and not self.allow_bot_inputs:
            raise ValueError("This workflow only accepts admin-fixed inputs")
        if supplied.keys() & self.fixed_inputs.keys():
            raise ValueError("Bot inputs cannot override admin-fixed inputs")
        return {**self.fixed_inputs, **supplied}


def _identity(repository: str, workflow: str) -> tuple[str, str]:
    repository = normalize_repo_full_name(repository).lower()
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml", workflow):
        raise ValueError("Provide a workflow filename, such as deploy.yml, without a path")
    return repository, f"{repository}/{workflow}"


@audit_tool(skip_read=True)
@access(_WRITE, per_call=lambda args: _READ if args.get("action") == "read" else _WRITE)
async def manage_workflow_allowlist(
    action: Literal["read", "set", "remove"],
    repository: str,
    workflow: str,
    ref: str | None = None,
    fixed_inputs: dict[str, str] | None = None,
    allow_bot_inputs: bool = False,
) -> dict[str, object]:
    """Configure one workflow's fixed ref and permitted inputs as an admin."""
    repository, key = _identity(repository, workflow)
    login = configurable().github_login
    if not login:
        raise ValueError("An authenticated requester is required")
    await require_repo_access_for_user(login, repository)
    if action == "set":
        rule = WorkflowRule(
            ref=ref or "", fixed_inputs=fixed_inputs or {}, allow_bot_inputs=allow_bot_inputs
        )
        await put_value(_NAMESPACE, key, rule.model_dump())
    elif action in {"read", "remove"}:
        if ref is not None or fixed_inputs is not None or allow_bot_inputs:
            raise ValueError("Configuration fields are only accepted for set")
        if action == "remove":
            await put_value(_NAMESPACE, key, {})
    else:
        raise ValueError("Action must be read, set, or remove")
    record = await get_value(_NAMESPACE, key)
    return {"repository": repository, "workflow": workflow, "rule": record or None}


@audit_tool()
@access(Policy(trusted="anywhere"))
async def trigger_github_workflow(
    repository: str,
    workflow: str,
    inputs: dict[str, str] | None = None,
) -> dict[str, object]:
    """Dispatch an allowlisted workflow at its admin-fixed ref."""
    repository, key = _identity(repository, workflow)
    login = configurable().github_login
    if not login:
        raise ValueError("An authenticated requester is required")
    record = await get_value(_NAMESPACE, key)
    if not record:
        raise ValueError("This workflow is not allowlisted")
    rule = WorkflowRule.model_validate(record)
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
