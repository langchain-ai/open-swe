"""Read or set a repository's approval mode on private admin surfaces."""

from typing import Literal

from agent.credential_scope import private_credential_login
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.approvals import fetch_approvals_md
from agent.review.styles import (
    REVIEW_STYLES,
    ApprovalMode,
    ReviewStylePromptUpdate,
    effective_approval_mode,
    normalize_repo_full_name,
)
from agent.tools.admin_gate import require_private_admin_surface


async def manage_review_approval_mode(
    action: Literal["read", "set"],
    repository: str,
    mode: ApprovalMode | None = None,
) -> dict[str, object]:
    """Read or set whether reviews of a repository skip, dry-run, or submit approvals."""
    if error := await require_private_admin_surface("manage approval modes"):
        raise ValueError(error)
    if action == "read" and mode is not None:
        raise ValueError("Omit mode when reading")
    repository = normalize_repo_full_name(repository)
    login = await private_credential_login()
    if not login:
        raise ValueError("An authenticated private thread owner is required")
    token = await require_repo_access_for_user(login, repository)
    if action == "set":
        record = await REVIEW_STYLES.update_prompts(
            repository, ReviewStylePromptUpdate(approval_mode=mode)
        )
    else:
        record = await REVIEW_STYLES.get(repository)
    owner, _, name = repository.partition("/")
    return {
        "repository": repository,
        "mode": effective_approval_mode(record),
        "approvals_file_on_default_branch": await fetch_approvals_md(owner, name, None, token=token)
        is not None,
    }
