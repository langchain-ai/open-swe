from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict

from managed_deepagents._channels.http.provider import handled_event_keys

if TYPE_CHECKING:
    from managed_deepagents._channels.github_types import GitHubAppOptions

PermissionLevel = Literal["read", "write"]

# The App permission each event needs: read to receive it, write to also
# reply where it happened.
EVENT_PERMISSIONS: dict[str, tuple[str, PermissionLevel]] = {
    "issue_comment.created": ("issues", "write"),
    "issue_comment.edited": ("issues", "write"),
    "issues.edited": ("issues", "write"),
    "issues.labeled": ("issues", "write"),
    "issues.opened": ("issues", "write"),
    "issues.reopened": ("issues", "write"),
    "pull_request.closed": ("pull_requests", "write"),
    "pull_request.converted_to_draft": ("pull_requests", "write"),
    "pull_request.edited": ("pull_requests", "write"),
    "pull_request.labeled": ("pull_requests", "write"),
    "pull_request.opened": ("pull_requests", "write"),
    "pull_request.ready_for_review": ("pull_requests", "write"),
    "pull_request.reopened": ("pull_requests", "write"),
    "pull_request.synchronize": ("pull_requests", "write"),
    "pull_request_review.dismissed": ("pull_requests", "write"),
    "pull_request_review.edited": ("pull_requests", "write"),
    "pull_request_review.submitted": ("pull_requests", "write"),
    "pull_request_review_comment.created": ("pull_requests", "write"),
    "pull_request_review_comment.edited": ("pull_requests", "write"),
    "check_run.completed": ("checks", "read"),
    "check_suite.completed": ("checks", "read"),
    "push": ("contents", "read"),
    "status": ("statuses", "read"),
    "workflow_run.completed": ("actions", "read"),
}

_MAX_PERMISSIONS = 50
# Keeps the App manifest and the deploy report readable.
_MAX_REPOSITORIES = 500
_PERMISSION_NAME = re.compile(r"[a-z][a-z_]{0,63}")
_GITHUB_LOGIN = re.compile(r"(?!-)(?!.*--)[A-Za-z0-9-]{1,39}(?<!-)")
_REPOSITORY_NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")


class AppPermission(TypedDict):
    """A permission the App asks for, with the handled events that need it."""

    name: str
    level: PermissionLevel
    events: list[str]
    """Empty when the level comes from ``app.permissions`` or is ``metadata``."""


class GitHubAppSetup(TypedDict):
    """What ``mda deploy`` needs to set up the GitHub App."""

    events: list[str]
    """The handled event keys, such as ``issues.opened``."""
    webhookEvents: list[str]
    """The webhook events the App subscribes to, such as ``issues``."""
    permissions: list[AppPermission]
    repositories: NotRequired[list[str]]
    """``owner/name``, lowercased and sorted; absent for every repository."""


def github_app_setup(
    handlers: Mapping[str, object], app: GitHubAppOptions | None
) -> GitHubAppSetup:
    """The App the channel's handlers and options need.

    The CLI reads it from the channel's manifest instead of working it out;
    the TypeScript runtime mirrors these rules in ``github/app-setup.ts``.
    """
    # A misspelled option would silently widen what the agent's token reaches.
    unknown = sorted(set(app or {}) - {"permissions", "repositories"})
    if unknown:
        raise ValueError(
            f"unknown GitHub app option {', '.join(unknown)}; use permissions or repositories"
        )
    events = [key for key in handled_event_keys(handlers) if key in EVENT_PERMISSIONS]
    setup = GitHubAppSetup(
        events=events,
        webhookEvents=sorted({key.split(".")[0] for key in events}),
        permissions=_app_permissions(events, (app or {}).get("permissions", {})),
    )
    if app is not None and "repositories" in app:
        setup["repositories"] = repository_allowlist(app["repositories"])
    return setup


def _app_permissions(events: list[str], extra: Mapping[str, str]) -> list[AppPermission]:
    """Every App gets ``metadata: read``; each handled event raises its
    permission to the level it needs, and ``app.permissions`` can raise it
    further."""
    _check_permissions(extra)
    permissions: dict[str, AppPermission] = {
        "metadata": AppPermission(name="metadata", level="read", events=[])
    }
    for key in events:
        name, level = EVENT_PERMISSIONS[key]
        current = permissions.get(name)
        if current is None or (current["level"] == "read" and level == "write"):
            permissions[name] = AppPermission(name=name, level=level, events=[key])
        elif current["level"] == level:
            current["events"].append(key)
    for name, level in extra.items():
        current = permissions.get(name)
        if current is None or (current["level"] == "read" and level == "write"):
            permissions[name] = AppPermission(
                name=name, level="write" if level == "write" else "read", events=[]
            )
    return [permissions[name] for name in sorted(permissions)]


def _check_permissions(permissions: Mapping[str, str]) -> None:
    if len(permissions) > _MAX_PERMISSIONS:
        raise ValueError(f"GitHub app.permissions must list at most {_MAX_PERMISSIONS} permissions")
    for name in sorted(permissions):
        if not _PERMISSION_NAME.fullmatch(name):
            raise ValueError(
                f"GitHub app.permissions has an invalid permission name '{name}'; "
                "use GitHub's names, such as contents"
            )
        if permissions[name] not in ("read", "write"):
            raise ValueError(f"GitHub app.permissions.{name} must be read or write")


def repository_allowlist(repositories: list[str]) -> list[str]:
    """The repositories the channel is limited to, lowercased since GitHub
    names are case-insensitive. One installation covers them, so they share
    an owner."""
    # An empty list could mean "none" or "all"; neither should be silent.
    if not repositories:
        raise ValueError(
            "GitHub app.repositories must list at least one repository; "
            "omit it to allow every repository"
        )
    valid: set[str] = set()
    for entry in repositories:
        repository = entry.strip()
        owner, _, name = repository.partition("/")
        if (
            not _GITHUB_LOGIN.fullmatch(owner)
            or not _REPOSITORY_NAME.fullmatch(name)
            or name in (".", "..")
        ):
            raise ValueError(f"GitHub app.repositories entry '{repository}' must be owner/name")
        valid.add(repository.lower())
    if len(valid) > _MAX_REPOSITORIES:
        raise ValueError(
            f"GitHub app.repositories must list at most {_MAX_REPOSITORIES} repositories"
        )
    owners = sorted({repository.split("/")[0] for repository in valid})
    if len(owners) > 1:
        raise ValueError(
            "GitHub app.repositories must all belong to one owner, because one "
            f"installation covers them (found: {', '.join(owners)})"
        )
    return sorted(valid)
