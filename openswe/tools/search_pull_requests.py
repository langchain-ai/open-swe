"""Search GitHub pull request text within a thread's repository access."""

import logging
from typing import Literal

import httpx2
from pydantic import BaseModel, Field

from openswe.github.http import GITHUB_API_BASE, github_client, github_request
from openswe.github.pull_request_status import pull_request_identity
from openswe.github.sandbox_access import workspace_token
from openswe.run_config import RunConfig
from openswe.sandboxes.state import thread_token_repositories
from openswe.tools.sandbox_preference import sandbox_only

logger = logging.getLogger(__name__)


class _User(BaseModel):
    login: str


class _TextMatch(BaseModel):
    fragment: str


class _PullRequest(BaseModel):
    number: int
    title: str
    repository_url: str
    state: Literal["open", "closed"]
    user: _User
    body: str | None = None
    updated_at: str
    draft: bool = False
    pull_request: dict[str, object] | None = None
    text_matches: list[_TextMatch] = Field(default_factory=list)


class _SearchResult(BaseModel):
    total_count: int
    incomplete_results: bool
    items: list[_PullRequest]


@sandbox_only
async def search_pull_requests(
    query: str,
    repo: str | None = None,
    per_page: int = 20,
    page: int = 1,
    sort: Literal["best-match", "created", "updated", "comments"] = "best-match",
    order: Literal["asc", "desc"] = "desc",
) -> dict[str, object]:
    """Implement the `search_pull_requests` tool."""
    cfg = RunConfig.from_runtime()
    repository = (repo or (cfg.repo.full_name if cfg.repo else "")).strip()
    if pull_request_identity({"repo_full_name": repository, "number": 1}) is None:
        return {"success": False, "error": "Specify a repository as owner/repo."}
    if not query.strip():
        return {"success": False, "error": "Search query must not be empty."}
    if not 1 <= per_page <= 100 or page < 1 or (page - 1) * per_page >= 1000:
        return {"success": False, "error": "Use per_page 1–100 and a page within 1,000 results."}
    if not cfg.thread_id:
        return {"success": False, "error": "Thread context unavailable."}
    allowed = await thread_token_repositories(cfg.thread_id)
    if allowed is not None and repository.casefold() not in {name.casefold() for name in allowed}:
        return {"success": False, "error": "Repository is outside this thread's GitHub access."}
    access = await workspace_token(
        cfg.workspace, repositories=[repository], permissions={"pull_requests": "read"}
    )
    if not access.token:
        return {"success": False, "error": "Repository is not accessible to the GitHub App."}
    params: dict[str, str | int] = {
        "q": f"{query.strip()} is:pr repo:{repository} in:title,body,comments",
        "per_page": per_page,
        "page": page,
        "order": order,
    }
    if sort != "best-match":
        params["sort"] = sort
    try:
        async with github_client(
            token=access.token, headers={"Accept": "application/vnd.github.text-match+json"}
        ) as client:
            response = await github_request(
                client, "GET", f"{GITHUB_API_BASE}/search/issues", params=params
            )
            response.raise_for_status()
            payload = _SearchResult.model_validate(response.json())
    except (httpx2.HTTPError, ValueError) as exc:
        logger.warning("GitHub PR search failed", exc_info=True, extra={"repository": repository})
        return {"success": False, "error": f"GitHub PR search failed: {exc}"}
    results: list[dict[str, object]] = []
    for item in payload.items:
        if item.pull_request is None or item.repository_url.casefold() != (
            f"{GITHUB_API_BASE}/repos/{repository}".casefold()
        ):
            continue
        results.append(
            {
                "number": item.number,
                "url": f"https://github.com/{repository}/pull/{item.number}",
                "title": item.title,
                "state": item.state,
                "draft": item.draft,
                "author": item.user.login,
                "updated_at": item.updated_at,
                "body": (item.body or "")[:4000],
                "body_truncated": len(item.body or "") > 4000,
                "fragments": [match.fragment[:4000] for match in item.text_matches],
            }
        )
    return {
        "success": True,
        "repo": repository,
        "total_count": payload.total_count,
        "incomplete_results": payload.incomplete_results,
        "results": results,
        "next_page": page + 1 if page * per_page < min(payload.total_count, 1000) else None,
    }
