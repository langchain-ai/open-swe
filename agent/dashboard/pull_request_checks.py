"""Batched check-run state for many pull requests in one GraphQL round trip.

The sidebar wants a failing/passing dot on every row that has a pull request,
which the per-thread ``pull_request_status`` path cannot serve without one
GitHub fan-out per row. This resolves up to `_MAX_PULL_REQUESTS` PRs in a
single aliased GraphQL query.

Callers supply ``owner/repo`` and a number directly rather than a thread id, so
the authorization boundary is GitHub itself: the query runs with the calling
user's own OAuth token and can only see what that account can already see. The
TTL cache is keyed by login for the same reason — a cached verdict for a
private PR is never served to an account that lacks access.
"""

import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal

import httpx
from typing_extensions import TypedDict

from agent.platforms.github.http import GITHUB_GRAPHQL, github_client, github_request

CheckState = Literal["failing", "passing", "pending", "unknown"]
PrState = Literal["open", "draft", "merged", "closed"]


# typing_extensions, not typing: pydantic rejects a stdlib TypedDict in a
# response model below Python 3.12, and this project supports 3.11.
class PullRequestState(TypedDict):
    """Live GitHub truth for one PR: check verdict plus open/merged/closed."""

    checks: CheckState
    state: PrState | None


_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
_MAX_PULL_REQUESTS = 50
_CACHE_TTL_SECONDS = 60.0
_CACHE_MAX_ENTRIES = 2000

_ROLLUP_STATES: dict[str, CheckState] = {
    "SUCCESS": "passing",
    "PENDING": "pending",
    "EXPECTED": "pending",
    "FAILURE": "failing",
    "ERROR": "failing",
}

_cache: dict[tuple[str, str, int], tuple[float, PullRequestState]] = {}


def pull_request_key(repo_full_name: str, number: int) -> str:
    return f"{repo_full_name}#{number}"


def _identity(record: object) -> tuple[str, str, int] | None:
    if not isinstance(record, Mapping):
        return None
    full_name = record.get("repoFullName")
    number = record.get("number")
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        return None
    owner, repo = full_name.split("/", 1)
    if (
        not _OWNER_PATTERN.fullmatch(owner)
        or not _REPO_PATTERN.fullmatch(repo)
        or repo in {".", ".."}
    ):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    return owner, repo, number


def _evict_expired(now: float) -> None:
    for key in [key for key, (expires, _) in _cache.items() if expires <= now]:
        _cache.pop(key, None)
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.pop(next(iter(_cache)), None)


def _checks_state(commit: object, rollup: object) -> CheckState:
    if rollup is None:
        # A PR the token can see but with no checks configured reads as passing
        # rather than unknown, so the row stays dot-free instead of ambiguous.
        return "passing" if isinstance(commit, Mapping) else "unknown"
    state = rollup.get("state") if isinstance(rollup, Mapping) else None
    return _ROLLUP_STATES.get(state, "unknown") if isinstance(state, str) else "unknown"


def _pr_state(pull: Mapping[str, Any]) -> PrState | None:
    state = pull.get("state")
    if state == "MERGED":
        return "merged"
    if state == "CLOSED":
        return "closed"
    if state != "OPEN":
        return None
    return "draft" if pull.get("isDraft") is True else "open"


def _pull_request_state(node: object) -> PullRequestState:
    pull = node.get("pullRequest") if isinstance(node, Mapping) else None
    if not isinstance(pull, Mapping):
        return {"checks": "unknown", "state": None}
    commits = pull.get("commits")
    nodes = commits.get("nodes") if isinstance(commits, Mapping) else None
    head = nodes[0] if isinstance(nodes, list) and nodes else None
    commit = head.get("commit") if isinstance(head, Mapping) else None
    rollup = commit.get("statusCheckRollup") if isinstance(commit, Mapping) else None
    return {"checks": _checks_state(commit, rollup), "state": _pr_state(pull)}


def _build_query(identities: Sequence[tuple[str, str, int]]) -> tuple[str, dict[str, Any]]:
    declarations: list[str] = []
    selections: list[str] = []
    variables: dict[str, Any] = {}
    for index, (owner, repo, number) in enumerate(identities):
        declarations.append(f"$o{index}:String!,$r{index}:String!,$n{index}:Int!")
        selections.append(
            f"p{index}: repository(owner:$o{index}, name:$r{index}) {{"
            f" pullRequest(number:$n{index}) {{"
            " state isDraft"
            " commits(last:1) { nodes { commit { statusCheckRollup { state } } } } } }"
        )
        variables[f"o{index}"] = owner
        variables[f"r{index}"] = repo
        variables[f"n{index}"] = number
    query = f"query BatchPullRequestChecks({','.join(declarations)}) {{ {' '.join(selections)} }}"
    return query, variables


async def get_pull_request_check_states(
    records: Sequence[object], login: str, token: str
) -> dict[str, PullRequestState]:
    """Return live state per requested pull request, keyed ``repo#number``."""
    now = time.monotonic()
    _evict_expired(now)

    results: dict[str, PullRequestState] = {}
    pending: list[tuple[str, str, int]] = []
    for record in records[:_MAX_PULL_REQUESTS]:
        identity = _identity(record)
        if identity is None:
            continue
        owner, repo, number = identity
        full_name = f"{owner}/{repo}"
        cached = _cache.get((login, full_name, number))
        if cached and cached[0] > now:
            results[pull_request_key(full_name, number)] = cached[1]
        else:
            pending.append(identity)

    if not pending:
        return results

    query, variables = _build_query(pending)
    payload: Any = None
    try:
        async with github_client(token=token) as client:
            response = await github_request(
                client, "POST", GITHUB_GRAPHQL, json={"query": query, "variables": variables}
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        payload = None

    data = payload.get("data") if isinstance(payload, Mapping) else None
    expires = time.monotonic() + _CACHE_TTL_SECONDS
    for index, (owner, repo, number) in enumerate(pending):
        full_name = f"{owner}/{repo}"
        resolved: PullRequestState = (
            _pull_request_state(data.get(f"p{index}"))
            if isinstance(data, Mapping)
            else {"checks": "unknown", "state": None}
        )
        results[pull_request_key(full_name, number)] = resolved
        if resolved["state"] is not None:
            _cache[(login, full_name, number)] = (expires, resolved)
    return results
