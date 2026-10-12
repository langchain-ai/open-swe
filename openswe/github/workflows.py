"""Workspace-scoped workflow dispatch policies."""

import re
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.database import postgres
from openswe.github.repositories import Repository
from openswe.review.styles import normalize_repo_full_name
from openswe.workspaces.rows import (
    WorkspaceRepositoryRow,
    WorkspaceRow,
    WorkspaceWorkflowPolicyRow,
)


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


def workflow_target(repository: str, workflow: str) -> str:
    """The repository's lowercase key, once ``workflow`` is a plain workflow filename."""
    stem, dot, extension = workflow.rpartition(".")
    if not (
        dot and extension in {"yml", "yaml"} and re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", stem)
    ):
        raise ValueError("Provide a workflow filename, such as deploy.yml, without a path")
    return normalize_repo_full_name(repository).lower()


class WorkflowPolicy(WorkflowRule):
    repository: str
    workflow: str


@dataclass(frozen=True)
class WorkflowPolicies:
    """One workspace's workflow policies, stored on its repository bindings."""

    workspace: str

    async def _binding(self, session: AsyncSession, repository: str) -> UUID:
        """The bound repository's id; raises when the workspace or binding is missing."""
        workspace_id = await session.scalar(
            select(WorkspaceRow.id).where(WorkspaceRow.slug == self.workspace)
        )
        if workspace_id is None:
            raise ValueError("Workspace not found")
        repository_id = await session.scalar(
            select(WorkspaceRepositoryRow.repository_id)
            .join(Repository, Repository.id == WorkspaceRepositoryRow.repository_id)
            .where(
                WorkspaceRepositoryRow.workspace_id == workspace_id, Repository.key == repository
            )
        )
        if repository_id is None:
            raise ValueError("Repository is not bound to this workspace")
        return repository_id

    async def require_repository(self, repository: str) -> None:
        async with postgres.session() as session:
            await self._binding(session, normalize_repo_full_name(repository).lower())

    async def read(self, repository: str, workflow: str) -> WorkflowPolicy | None:
        key = workflow_target(repository, workflow)
        async with postgres.session() as session:
            repository_id = await self._binding(session, key)
            row = (
                await session.execute(
                    _POLICIES.where(
                        WorkspaceWorkflowPolicyRow.repository_id == repository_id,
                        WorkspaceWorkflowPolicyRow.workflow == workflow,
                    )
                )
            ).one_or_none()
        return _policy(*row) if row else None

    async def list_policies(self) -> list[WorkflowPolicy]:
        async with postgres.session() as session:
            if not await session.scalar(
                select(WorkspaceRow.id).where(WorkspaceRow.slug == self.workspace)
            ):
                raise ValueError("Workspace not found")
            rows = (
                await session.execute(
                    _POLICIES.join(
                        WorkspaceRow, WorkspaceRow.id == WorkspaceRepositoryRow.workspace_id
                    )
                    .where(WorkspaceRow.slug == self.workspace)
                    .order_by(Repository.key, WorkspaceWorkflowPolicyRow.workflow)
                )
            ).all()
        return [_policy(*row) for row in rows]

    async def set(self, repository: str, workflow: str, rule: WorkflowRule) -> WorkflowPolicy:
        key = workflow_target(repository, workflow)
        values = rule.model_dump()
        async with postgres.session() as session:
            repository_id = await self._binding(session, key)
            await session.execute(
                insert(WorkspaceWorkflowPolicyRow)
                .values(repository_id=repository_id, workflow=workflow, **values)
                .on_conflict_do_update(
                    index_elements=["repository_id", "workflow"],
                    set_={**values, "updated_at": func.now()},
                )
            )
        policy = await self.read(repository, workflow)
        if policy is None:
            raise RuntimeError("Saved workflow policy could not be read back")
        return policy

    async def remove(self, repository: str, workflow: str) -> None:
        key = workflow_target(repository, workflow)
        async with postgres.session() as session:
            repository_id = await self._binding(session, key)
            await session.execute(
                delete(WorkspaceWorkflowPolicyRow).where(
                    WorkspaceWorkflowPolicyRow.repository_id == repository_id,
                    WorkspaceWorkflowPolicyRow.workflow == workflow,
                )
            )


_POLICIES = (
    select(Repository.full_name, WorkspaceWorkflowPolicyRow)
    .join(
        WorkspaceRepositoryRow,
        WorkspaceRepositoryRow.repository_id == WorkspaceWorkflowPolicyRow.repository_id,
    )
    .join(Repository, Repository.id == WorkspaceWorkflowPolicyRow.repository_id)
)


def _policy(repository: str, row: WorkspaceWorkflowPolicyRow) -> WorkflowPolicy:
    return WorkflowPolicy(
        repository=repository,
        workflow=row.workflow,
        ref=row.ref,
        fixed_inputs=dict(row.fixed_inputs),
        allow_bot_inputs=row.allow_bot_inputs,
        admin_only=row.admin_only,
    )
