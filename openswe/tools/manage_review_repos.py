"""Read repository auto-review opt-ins or change them through authorized admin writes."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.review.enabled_repos import list_enabled_review_repos, set_review_repo_enabled
from openswe.review.styles import normalize_repo_full_name
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import configurable
from openswe.tools.mcp_exposure import expose_mcp

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("repository", "enabled"))


@expose_mcp(access="admin")
@audit_tool(skip_read=True)
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_review_repos(
    action: Literal["read", "set"],
    repository: str,
    enabled: bool | None = None,
) -> dict[str, object]:
    """Read or set a repository's automatic review opt-in without changing other settings."""
    if action == "set":
        if not isinstance(enabled, bool):
            raise ValueError("Provide enabled as true or false when setting")
    elif action == "read":
        if enabled is not None:
            raise ValueError("Omit enabled when reading")
    else:
        raise ValueError("Action must be read or set")
    repository = normalize_repo_full_name(repository).lower()
    login = configurable().github_login
    if not login:
        raise ValueError("An authenticated requester is required")
    await require_repo_access_for_user(login, repository)
    if action == "set" and enabled is not None:
        repos = await set_review_repo_enabled(repository, enabled)
    else:
        repos = await list_enabled_review_repos()
    return {"repository": repository, "enabled": repository in {repo.lower() for repo in repos}}
