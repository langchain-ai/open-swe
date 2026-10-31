"""The repository a channel message names, and whether Open SWE may work in it.

A copy of ``openswe.utils.repo.extract_repo_from_text`` and
``openswe.webhooks.common.is_repo_allowed`` (AGENTS.md's exception for channel
input): ``mda/`` never imports the backend. The backend checks a run's repository
again before it signs the run, so these only decide what the channel asks for and
what it tells a person. ``tests/agent/test_mda_repositories.py`` holds the parser to
the backend's. Standard library only, so that test can load it.
"""

import re
from collections.abc import Collection
from typing import Final

_REPO_MENTION: Final = re.compile(r"repo[: ]([a-zA-Z0-9_.\-/]+)")
_EXPLICIT_REPO: Final = re.compile(r"repo:([a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+)")
# github.com itself, not gist.github.com or another subdomain.
_GITHUB_URL: Final = re.compile(
    r"(?<![\w.-])(?:www\.)?github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)"
)
# First path segments that are GitHub's own pages and uploads, never an owner.
_NOT_OWNERS: Final = frozenset(
    {
        "about",
        "account",
        "advisories",
        "apps",
        "assets",
        "codespaces",
        "collections",
        "enterprises",
        "features",
        "marketplace",
        "notifications",
        "orgs",
        "organizations",
        "settings",
        "sponsors",
        "topics",
        "user-attachments",
        "users",
    }
)


def repository_in(
    text: str, default_owner: str | None, *, explicit_only: bool = False
) -> str | None:
    """The ``owner/name`` that ``text`` names, if any.

    ``repo:owner/name`` or ``repo owner/name`` first (``repo name`` takes
    ``default_owner``), then the first github.com link to a repository: not an
    uploaded image, a gist or one of GitHub's own pages. ``explicit_only`` accepts
    only ``repo:owner/name``, for prose written for people, such as an issue's
    description, where "repo <word>" is ordinary English rather than a choice.
    """
    owner: str | None = None
    name: str | None = None
    if "repo:" in text or "repo " in text:
        match = (_EXPLICIT_REPO if explicit_only else _REPO_MENTION).search(text)
        if match:
            value = match.group(1).rstrip("/")
            if "/" in value:
                owner, name = value.split("/", 1)
            else:
                owner, name = default_owner, value
    if not owner or not name:
        for github_match in _GITHUB_URL.finditer(text):
            if github_match.group(1).lower() not in _NOT_OWNERS:
                owner, name = github_match.groups()
                break
    return f"{owner}/{name}" if owner and name else None


def is_allowed(repository: str, *, orgs: Collection[str], repos: Collection[str]) -> bool:
    """Whether ``repository`` is in the allowlist; with none configured, every one is."""
    if not orgs and not repos:
        return True
    owner, _, name = repository.lower().partition("/")
    return owner in orgs or f"{owner}/{name}" in repos
