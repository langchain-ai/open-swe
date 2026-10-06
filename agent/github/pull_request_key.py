"""One pull request as a single string, ``owner/repo/number``, lowercased as GitHub matches it."""

from typing import NewType

PullRequestKey = NewType("PullRequestKey", str)


def pull_request_key(owner: str, repo: str, number: int) -> PullRequestKey:
    return PullRequestKey(f"{owner}/{repo}/{number}".lower())


def parse_pull_request_key(key: str) -> tuple[str, str, int] | None:
    owner, _, rest = key.partition("/")
    repo, _, number = rest.partition("/")
    if not owner or not repo or not number.isdigit():
        return None
    return owner, repo, int(number)
