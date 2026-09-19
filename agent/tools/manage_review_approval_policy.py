"""Manage app-owned approval policy from an authenticated private admin thread."""

from typing import Literal

from agent.credential_scope import private_credential_login
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.approval_settings import (
    PolicyDefinition,
    get_policy_settings,
    normalize_repository,
    save_policy_settings,
)
from agent.tools.admin_gate import require_private_admin_surface


async def manage_review_approval_policy(
    action: Literal["read", "save", "reset"],
    repository: str | None = None,
    policy: PolicyDefinition | None = None,
    expected_version: str | None = None,
) -> dict[str, object]:
    """Read, save, or reset Open SWE approval requirements."""
    if error := await require_private_admin_surface("manage approval policies"):
        raise ValueError(error)
    login = await private_credential_login()
    if not login:
        raise ValueError(
            "Approval policy settings require the authenticated owner of a private thread"
        )
    repository = normalize_repository(repository)
    if repository:
        await require_repo_access_for_user(login, repository)
    if action == "read":
        view = await get_policy_settings(repository)
    else:
        if not expected_version:
            raise ValueError("Read the current effective_version before changing approval policy")
        if action == "save" and policy is None:
            raise ValueError("Saving requires a policy")
        if action == "reset" and policy is not None:
            raise ValueError("Reset must not include a policy")
        view = await save_policy_settings(
            repository, policy=policy, expected_version=expected_version, login=login
        )
    return view.model_copy(update={"can_edit": True}).model_dump(mode="json")
