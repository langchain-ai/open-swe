"""Which repositories a thread's GitHub token may reach.

A sandbox token covers the whole GitHub App installation unless its thread was
started by an event or automation on a public repository, where the content the
agent acts on is the most likely to come from outside. Such a thread records
its repository in its metadata when it is created, and every token its sandbox
gets is narrowed to it.
"""

from collections.abc import Mapping

GITHUB_TOKEN_REPOSITORIES_KEY = "github_token_repositories"


def event_token_repositories(owner: str, name: str, *, private: bool | None) -> list[str] | None:
    """The scope for a thread an event on ``owner/name`` starts; ``None`` is the installation.

    Only a repository known to be private keeps the installation-wide token, so
    an event whose visibility is unknown is narrowed.
    """
    if private is True:
        return None
    return [f"{owner}/{name}"]


def token_repositories_from_metadata(metadata: Mapping[str, object]) -> list[str] | None:
    """The recorded scope, ``None`` when there is none, and no access when it is unreadable."""
    if GITHUB_TOKEN_REPOSITORIES_KEY not in metadata:
        return None
    value = metadata[GITHUB_TOKEN_REPOSITORIES_KEY]
    if not isinstance(value, list) or not all(isinstance(repo, str) for repo in value):
        return []
    return [repo for repo in value if isinstance(repo, str)]
