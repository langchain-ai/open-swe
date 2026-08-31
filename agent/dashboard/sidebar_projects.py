"""Per-user sidebar projects.

A project is created the first time a repo shows up in the user's threads and
lives until the user deletes it. Deletion leaves a tombstone rather than
removing the record: the repo's threads keep existing and would otherwise
re-create the project on the next sidebar load, so "delete" would never stick.
"""

from typing import Any

from agent.store import now_ms, put_value, search_values

SIDEBAR_PROJECTS_NAMESPACE = "sidebar_projects"
_MAX_PROJECTS = 500


def _namespace(login: str) -> list[str]:
    return [SIDEBAR_PROJECTS_NAMESPACE, login]


def project_key(repo_full_name: str) -> str:
    return repo_full_name.strip().lower()


def _record(repo_full_name: str, *, deleted_at_ms: int | None) -> dict[str, Any]:
    return {
        "repo_full_name": repo_full_name,
        "created_at_ms": now_ms(),
        "deleted_at_ms": deleted_at_ms,
    }


async def list_project_records(login: str) -> dict[str, dict[str, Any]]:
    """Every project record the user has, deleted ones included, keyed by repo."""
    records = await search_values(_namespace(login), limit=_MAX_PROJECTS)
    return {
        project_key(repo): record
        for record in records
        if isinstance((repo := record.get("repo_full_name")), str) and repo.count("/") == 1
    }


async def ensure_projects(login: str, repos: list[str]) -> list[str]:
    """Create projects for repos the user has never had one for. Returns the new repos."""
    known = await list_project_records(login)
    created: list[str] = []
    for repo in repos:
        key = project_key(repo)
        if not key or repo.count("/") != 1 or key in known or len(known) >= _MAX_PROJECTS:
            continue
        record = _record(repo, deleted_at_ms=None)
        await put_value(_namespace(login), key, record)
        known[key] = record
        created.append(repo)
    return created


async def delete_project(login: str, repo_full_name: str) -> None:
    key = project_key(repo_full_name)
    record = (await list_project_records(login)).get(key) or _record(
        repo_full_name, deleted_at_ms=None
    )
    await put_value(_namespace(login), key, {**record, "deleted_at_ms": now_ms()})


async def restore_project(login: str, repo_full_name: str) -> None:
    key = project_key(repo_full_name)
    record = (await list_project_records(login)).get(key) or _record(
        repo_full_name, deleted_at_ms=None
    )
    await put_value(
        _namespace(login),
        key,
        {**record, "repo_full_name": repo_full_name, "deleted_at_ms": None},
    )
