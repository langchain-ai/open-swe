"""Read a repository's stored approval mode; assessments stay advisory either way."""

from typing import Literal

from agent.audit_logs.tools import audit_tool
from agent.dashboard.repo_access import require_repo_access_for_user
from agent.review.approvals import fetch_approvals_md
from agent.review.styles import (
    REVIEW_STYLES,
    effective_approval_mode,
    normalize_repo_full_name,
)
from agent.tools.access import Policy, access
from agent.tools.admin_gate import configurable

_READ = Policy(trusted="admin_surface", actor="admin")


@audit_tool(skip_read=True)
@access(_READ)
async def manage_review_approval_mode(
    action: Literal["read"],
    repository: str,
) -> dict[str, object]:
    """Read whether a repository's stored approval mode is off, dry-run, or approve."""
    if action != "read":
        raise ValueError("Approval modes are read-only; change them through a pull request")
    repository = normalize_repo_full_name(repository)
    login = configurable().github_login
    if not login:
        raise ValueError("An authenticated requester is required")
    token = await require_repo_access_for_user(login, repository)
    record = await REVIEW_STYLES.get(repository)
    owner, _, name = repository.partition("/")
    return {
        "repository": repository,
        "mode": effective_approval_mode(record),
        "approvals_file_on_default_branch": await fetch_approvals_md(owner, name, None, token=token)
        is not None,
        "note": "Assessments are advisory: reviews never submit a GitHub approval.",
    }
