"""The repositories a thread works in, as thread metadata carries them.

``metadata["repos"]`` is the record: a list of ``{"owner", "name"}`` in the
order the repositories were added. ``repo_owner``/``repo_name`` and ``repo``
are the single-repository shapes older threads were written with; readers fall
back to them and writers never produce them.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from agent.run_config import Repo, dedupe_repos, parse_repos

REPOS_METADATA_KEY = "repos"
REPO_EXPLICITLY_NONE_METADATA_KEY = "repo_explicitly_none"


def thread_repos(metadata: Mapping[str, Any]) -> list[Repo]:
    """Every repository the thread targets, in the order they were added."""
    listed = parse_repos(metadata.get(REPOS_METADATA_KEY))
    if listed:
        return listed
    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return [Repo(owner=owner, name=name)]
    legacy = Repo.parse(metadata.get("repo"))
    return [legacy] if legacy else []


def thread_repo_full_names(metadata: Mapping[str, Any]) -> list[str]:
    return [repo.full_name for repo in thread_repos(metadata)]


def repos_metadata(repos: Iterable[Repo | None]) -> list[dict[str, str]]:
    """The JSON value to store under ``metadata["repos"]``."""
    return [{"owner": repo.owner, "name": repo.name} for repo in dedupe_repos(repos)]


def with_thread_repo(metadata: Mapping[str, Any], repo: Repo) -> list[Repo]:
    """The thread's repositories with ``repo`` appended, unchanged if already present."""
    return dedupe_repos([*thread_repos(metadata), repo])
