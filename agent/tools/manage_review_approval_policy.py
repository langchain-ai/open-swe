"""Edit approval criteria through the existing reviewer settings stores."""

from typing import Literal

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    get_instance_settings,
    upsert_instance_settings,
    upsert_workspace_overrides,
    workspace_settings_view,
)
from agent.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate, normalize_repo_full_name
from agent.tools.access import Policy, access, ack
from agent.tools.admin_gate import configurable
from agent.workspaces.store import WORKSPACES, slugify

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("repository", "workspace"))


@access(_WRITE, per_call=lambda args: _READ if args.get("action") == "read" else _WRITE)
async def manage_review_approval_policy(
    action: Literal["read", "save", "reset"],
    policy: str | None = None,
    repository: str | None = None,
    workspace: str | None = None,
    auto_approve: bool | Literal["inherit"] | None = None,
) -> dict[str, object]:
    """Read, set, or reset approval criteria independently of review guidelines."""
    if repository and workspace:
        raise ValueError("Choose a repository or workspace, not both")
    if action == "save" and policy is None and auto_approve is None:
        raise ValueError("Provide a policy or auto_approve setting")
    if action == "save" and policy is not None and not policy.strip():
        raise ValueError("Saving requires a non-empty policy; use reset to inherit")
    if repository and auto_approve is not None:
        raise ValueError("Automatic approval is configured at the instance or workspace level")
    changes: dict[str, str | bool | None] = {}
    if action == "reset" or policy is not None:
        changes["approval_policy"] = policy if action == "save" else None
    if action == "save" and auto_approve is not None:
        changes["review_auto_approve"] = None if auto_approve == "inherit" else auto_approve
    update = WorkspaceSettingsUpdate.model_validate(changes)
    if repository:
        repository = normalize_repo_full_name(repository)
        login = configurable().github_login
        if not login:
            raise ValueError("An authenticated requester is required")
        await require_repo_access_for_user(login, repository)
        record = await REVIEW_STYLES.get(repository)
        if action != "read":
            record = await REVIEW_STYLES.update_prompts(
                repository, ReviewStylePromptUpdate(approval_policy=update.approval_policy)
            )
        return {
            "repository": repository,
            "approval_policy": record.approval_policy if record else None,
        }
    if workspace:
        workspace = slugify(workspace)
        if await WORKSPACES.get(workspace) is None:
            raise ValueError("Workspace not found")
        view = await workspace_settings_view(workspace)
        if action != "read":
            view = await upsert_workspace_overrides(
                workspace,
                WorkspaceSettingsUpdate.model_validate(
                    {
                        **view["overrides"],
                        **update.model_dump(exclude_unset=True),
                    }
                ),
            )
        return {
            "workspace": workspace,
            "approval_policy": view["effective"].get("approval_policy"),
            "auto_approve": view["effective"].get("review_auto_approve", False),
        }
    settings = await get_instance_settings()
    if action != "read":
        await upsert_instance_settings(
            WorkspaceSettingsUpdate.model_validate(
                {
                    **settings,
                    **update.model_dump(exclude_unset=True),
                }
            )
        )
        settings = await get_instance_settings()
    return {
        "approval_policy": settings.get("approval_policy"),
        "auto_approve": settings.get("review_auto_approve", False),
    }
