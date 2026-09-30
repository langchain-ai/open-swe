"""Read or set a repository's approval mode on private admin surfaces."""

from typing import Literal

from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.approval_rules import refresh_approval_program
from agent.review.approvals import fetch_approvals_md
from agent.review.styles import (
    REVIEW_STYLES,
    ApprovalMode,
    ReviewStylePromptUpdate,
    effective_approval_mode,
    normalize_repo_full_name,
)
from agent.tools.access import Policy, access, ack
from agent.tools.admin_gate import configurable

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("repository", "mode"))


@access(_WRITE, per_call=lambda args: _READ if args.get("action") == "read" else _WRITE)
async def manage_review_approval_mode(
    action: Literal["read", "set", "refresh"],
    repository: str,
    mode: ApprovalMode | None = None,
) -> dict[str, object]:
    """Read or set approval mode, or regenerate trusted deterministic approval rules."""
    if action != "set" and mode is not None:
        raise ValueError("Omit mode unless setting it")
    repository = normalize_repo_full_name(repository)
    login = configurable().github_login
    if not login:
        raise ValueError("An authenticated requester is required")
    token = await require_repo_access_for_user(login, repository)
    if action == "set":
        record = await REVIEW_STYLES.update_prompts(
            repository, ReviewStylePromptUpdate(approval_mode=mode)
        )
    else:
        record = await REVIEW_STYLES.get(repository)
    owner, _, name = repository.partition("/")
    if action == "refresh":
        program = await refresh_approval_program(owner, name, token=token)
        return {"repository": repository, "program": program.model_dump(mode="json")}
    return {
        "repository": repository,
        "mode": effective_approval_mode(record),
        "approvals_file_on_default_branch": await fetch_approvals_md(owner, name, None, token=token)
        is not None,
    }
