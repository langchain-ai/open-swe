"""Read approval modes privately or change them through authorized admin writes."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.review.approvals import fetch_approvals_md
from openswe.review.styles import (
    REVIEW_STYLES,
    ApprovalMode,
    ReviewStylePromptUpdate,
    effective_approval_mode,
    normalize_repo_full_name,
)
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("repository", "mode"))


@expose_mcp(access="admin")
@audit_tool(skip_read=True)
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_review_approval_mode(
    action: Literal["read", "set"],
    repository: str,
    mode: ApprovalMode | None = None,
) -> dict[str, object]:
    """Read or set whether reviews of a repository skip, dry-run, or submit approvals."""
    if action == "read" and mode is not None:
        raise ValueError("Omit mode when reading")
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
    return {
        "repository": repository,
        "mode": effective_approval_mode(record),
        "approvals_file_on_default_branch": await fetch_approvals_md(owner, name, None, token=token)
        is not None,
    }
