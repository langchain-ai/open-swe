"""Workspace-scoped workflow dispatch policies."""

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from openswe.review.styles import normalize_repo_full_name
from openswe.store import delete_value, get_value, put_value, search_all_values
from openswe.workspaces.store import WORKSPACES


class WorkflowRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ref: str = Field(min_length=1)
    fixed_inputs: dict[str, str] = Field(default_factory=dict)
    allow_bot_inputs: bool = False
    admin_only: bool = False

    def dispatch_inputs(self, supplied: dict[str, str] | None) -> dict[str, str]:
        supplied = supplied or {}
        if supplied and not self.allow_bot_inputs:
            raise ValueError("This workflow only accepts admin-fixed inputs")
        if supplied.keys() & self.fixed_inputs.keys():
            raise ValueError("Bot inputs cannot override admin-fixed inputs")
        return {**self.fixed_inputs, **supplied}


def workflow_identity(repository: str, workflow: str) -> tuple[str, str]:
    repository = normalize_repo_full_name(repository).lower()
    stem, dot, extension = workflow.rpartition(".")
    if not (
        dot and extension in {"yml", "yaml"} and re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", stem)
    ):
        raise ValueError("Provide a workflow filename, such as deploy.yml, without a path")
    return repository, f"{repository}/{workflow}"


class WorkflowPolicy(WorkflowRule):
    repository: str
    workflow: str


@dataclass(frozen=True)
class WorkflowPolicies:
    workspace: str

    @property
    def namespace(self) -> list[str]:
        return ["github_workflow_allowlist", self.workspace]

    async def require_repository(self, repository: str) -> None:
        record = await WORKSPACES.get(self.workspace)
        if record is None:
            raise ValueError("Workspace not found")
        if repository.lower() not in {repo.lower() for repo in record.repos}:
            raise ValueError("Repository is not bound to this workspace")

    async def read(self, repository: str, workflow: str) -> WorkflowPolicy | None:
        repository, key = workflow_identity(repository, workflow)
        await self.require_repository(repository)
        record = await get_value(self.namespace, key)
        return WorkflowPolicy.model_validate(record) if record else None

    async def list_policies(self) -> list[WorkflowPolicy]:
        if not await WORKSPACES.slug_exists(self.workspace):
            raise ValueError("Workspace not found")
        return [
            WorkflowPolicy.model_validate(row) for row in await search_all_values(self.namespace)
        ]

    async def set(self, repository: str, workflow: str, rule: WorkflowRule) -> WorkflowPolicy:
        repository, key = workflow_identity(repository, workflow)
        await self.require_repository(repository)
        policy = WorkflowPolicy(repository=repository, workflow=workflow, **rule.model_dump())
        await put_value(self.namespace, key, policy.model_dump())
        return policy

    async def remove(self, repository: str, workflow: str) -> None:
        """Revoke a policy, even one whose repository has since been unbound."""
        _, key = workflow_identity(repository, workflow)
        if not await WORKSPACES.slug_exists(self.workspace):
            raise ValueError("Workspace not found")
        await delete_value(self.namespace, key)
