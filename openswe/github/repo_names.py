"""GitHub repository names as ``owner/repo``."""

from typing import NewType

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
