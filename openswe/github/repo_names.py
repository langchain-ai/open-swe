"""GitHub repository names as ``owner/repo``."""

from typing import NewType

from openswe.config import ENV

RepoFullName = NewType("RepoFullName", str)
"""A GitHub repository as ``owner/repo``, from ``normalize_repo_full_name``."""


def normalize_repo_full_name(raw: str) -> RepoFullName:
    """Normalize user input to ``owner/repo``."""
    v = raw.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if v.lower().startswith(prefix):
            v = v[len(prefix) :]
    v = v.strip("/")
    if v.endswith(".git"):
        v = v[:-4]
    parts = [p for p in v.split("/") if p]
    if len(parts) != 2:
        raise ValueError("full_name must be owner/repo")
    return RepoFullName(f"{parts[0]}/{parts[1]}")


def controlling_org() -> str | None:
    """The unambiguous organization controlling this deployment."""
    orgs = tuple(dict.fromkeys(org.lower() for org in ENV.ALLOWED_GITHUB_ORGS.get_list()))
    return orgs[0] if len(orgs) == 1 else None


def display_repo_name(full_name: str) -> str:
    """Shorten only the controlling organization in a display label."""
    owner, separator, name = full_name.partition("/")
    return (
        name
        if separator and name and "/" not in name and owner.lower() == controlling_org()
        else full_name
    )
