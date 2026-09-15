"""Check-run state for many pull requests at once, behind the sidebar dot.

The sidebar wants a failing/passing dot on every row that has a pull request,
which the per-thread ``pull_request_status`` path cannot serve without one
GitHub fan-out per row. Stored rows recent enough to answer for GitHub are read
from PostgreSQL in one query; whatever is left resolves in a single aliased
GraphQL query of up to `_MAX_PULL_REQUESTS` PRs and is written back in the
background.

Callers supply ``owner/repo`` and a number directly rather than a thread id, so
nothing upstream has decided what this caller may see. GitHub decides it for
the live path: the query runs with the calling user's own OAuth token and can
only see what that account can already see, and the TTL cache is keyed by login
so a cached verdict is never served to another account. The tables know no such
thing, so a stored row is served only when the repository is recorded public,
or when the caller's own cached repository list names it; otherwise the PR is
treated as unstored and GitHub is asked with the caller's token.
"""

import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

import httpx2
from pydantic import BaseModel, Field, ValidationError

from agent.database import postgres
from agent.github.http import GITHUB_GRAPHQL, github_client, github_request
from agent.github.pull_request_sync import schedule_pull_request_sync
from agent.github.pull_request_terms import (
    CheckState,
    identity_key,
    parse_identity,
    pull_request_max_age,
)
from agent.github.pull_requests import PullRequest
from agent.github.repo_cache import read_cached_repos

logger = logging.getLogger(__name__)

PrState = Literal["open", "draft", "merged", "closed"]


class PullRequestState(TypedDict):
    """One PR as the sidebar renders it: check verdict plus open/merged/closed."""

    checks: CheckState
    state: PrState | None


class _CachedRepo(BaseModel):
    full_name: str = ""


class _CachedRepoList(BaseModel):
    repositories: list[_CachedRepo] = Field(default_factory=list)


_MAX_PULL_REQUESTS = 50
_MAX_WRITE_BACK = 10
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
    return parse_identity(record.get("repoFullName"), record.get("number"))


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


async def _accessible_repo_keys(login: str) -> frozenset[str]:
    """Lowercased repositories the caller's cached repo list names."""
    cached = await read_cached_repos(login)
    if cached is None:
        return frozenset()
    try:
        listing = _CachedRepoList.model_validate(cached[0])
    except ValidationError:
        return frozenset()
    return frozenset(repo.full_name.lower() for repo in listing.repositories if repo.full_name)


async def _servable_rows(
    identities: Sequence[tuple[str, str, int]], login: str
) -> dict[tuple[str, str, int], PullRequest]:
    """Stored rows this caller may be served: fresh, and on a repository they can see."""
    if not identities or not postgres.configured():
        return {}
    max_age = pull_request_max_age()
    try:
        rows = await PullRequest.get_all(identities)
    except Exception:
        logger.warning(
            "Sidebar pull request check states could not read stored rows", exc_info=True
        )
        return {}
    fresh = [row for row in rows if row.synced_within(max_age)]
    restricted = any(row.repository.private is not False for row in fresh)
    accessible = await _accessible_repo_keys(login) if restricted else frozenset()
    return {
        identity_key((row.owner, row.repo, row.number)): row
        for row in fresh
        if row.repository.private is False or row.repository.key in accessible
    }


async def get_pull_request_check_states(
    records: Sequence[object], login: str, token: str
) -> dict[str, PullRequestState]:
    """Return state per requested pull request, keyed ``repo#number``, stored rows first."""
    now = time.monotonic()
    _evict_expired(now)

    results: dict[str, PullRequestState] = {}
    unresolved: list[tuple[str, str, int]] = []
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
            unresolved.append(identity)

    stored = await _servable_rows(unresolved, login)
    expires = time.monotonic() + _CACHE_TTL_SECONDS
    pending: list[tuple[str, str, int]] = []
    for identity in unresolved:
        owner, repo, number = identity
        row = stored.get(identity_key(identity))
        if row is None:
            pending.append(identity)
            continue
        full_name = f"{owner}/{repo}"
        state: PullRequestState = {"checks": row.check_state, "state": row.state}
        results[pull_request_key(full_name, number)] = state
        _cache[(login, full_name, number)] = (expires, state)

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
    except httpx2.HTTPError, ValueError:
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

    # A sidebar that has just been opened on a cold cache would otherwise burst
    # a full sync per row; past the cap the rows are left to the next read.
    if len(pending) <= _MAX_WRITE_BACK:
        for owner, repo, number in pending:
            schedule_pull_request_sync(owner, repo, number, token=token)
    return results
