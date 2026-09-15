"""Admin endpoints that force a pull request re-read from GitHub.

The sweep covers missed webhooks on a cadence; these endpoints are the manual
override for when an operator does not want to wait for it.
"""

from datetime import datetime
from typing import Any, Self

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.dashboard.deps import ADMIN_DEP
from agent.github.app import get_github_app_installation_token
from agent.github.comments import PrState
from agent.github.pull_request_status import pull_request_identity
from agent.github.pull_request_sweep import SweepReport, run_sweep_once, sweep_repository
from agent.github.pull_request_sync import sync_pull_request
from agent.github.pull_requests import PullRequest

router = APIRouter(tags=["github"])


class PullRequestSyncResult(BaseModel):
    repo_full_name: str
    number: int
    state: PrState
    draft: bool
    head_sha: str
    mergeable_state: str
    check_count: int
    unresolved_review_thread_count: int
    last_synced_at: datetime | None

    @classmethod
    def of(cls, row: PullRequest) -> Self:
        return cls(
            repo_full_name=row.repo_full_name,
            number=row.number,
            state=row.state,
            draft=row.draft,
            head_sha=row.head_sha,
            mergeable_state=row.mergeable_state,
            check_count=len(row.head_checks),
            unresolved_review_thread_count=len(row.unresolved_review_threads),
            last_synced_at=row.last_synced_at,
        )


def _identity(owner: str, repo: str, number: int) -> tuple[str, str, int]:
    identity = pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number})
    if identity is None:
        raise HTTPException(404, "unknown pull request")
    return identity


def _repository(owner: str, repo: str) -> tuple[str, str]:
    validated_owner, validated_repo, _ = _identity(owner, repo, 1)
    return validated_owner, validated_repo


async def _token(owner: str, repo: str) -> str:
    token = await get_github_app_installation_token(repositories=[f"{owner}/{repo}"])
    if token is None:
        raise HTTPException(502, "no GitHub App installation for this repository")
    return token


@router.post("/github/pull-requests/sync-stale")
async def api_sync_stale_pull_requests(_admin: dict[str, Any] = ADMIN_DEP) -> SweepReport:
    return await run_sweep_once()


@router.post("/github/pull-requests/{owner}/{repo}/{number}/sync")
async def api_sync_pull_request(
    owner: str,
    repo: str,
    number: int,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> PullRequestSyncResult:
    owner, repo, number = _identity(owner, repo, number)
    row = await sync_pull_request(owner, repo, number, token=await _token(owner, repo))
    if row is None:
        raise HTTPException(502, "GitHub did not return this pull request")
    return PullRequestSyncResult.of(row)


@router.post("/github/repositories/{owner}/{repo}/sync")
async def api_sync_repository(
    owner: str,
    repo: str,
    _admin: dict[str, Any] = ADMIN_DEP,
) -> SweepReport:
    owner, repo = _repository(owner, repo)
    return await sweep_repository(owner, repo)
