import asyncio
from typing import Any

import httpx
from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token

GITHUB_API_BASE = "https://api.github.com"
PER_PAGE = 100
HTTP_TIMEOUT_SECONDS = 30.0

FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required")
RERUNNABLE_CONCLUSIONS = ("failure", "timed_out", "cancelled")


def _get_repo_config() -> dict[str, str]:
    config = get_config()
    return config.get("configurable", {}).get("repo", {})


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get_token() -> str | None:
    return await get_github_app_installation_token()


def _repo_url(repo_config: dict[str, str]) -> str:
    owner = repo_config.get("owner", "")
    name = repo_config.get("name", "")
    return f"{GITHUB_API_BASE}/repos/{owner}/{name}"


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS))


async def _fetch_paginated_items(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    item_key: str,
    params: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]] | None, int | None, str | None]:
    items: list[dict[str, Any]] = []
    total_count: int | None = None
    page = 1

    while True:
        # Reserved pagination params take precedence over caller-supplied params
        # so the end-of-pagination check below stays consistent with PER_PAGE.
        page_params = dict(params) if params else {}
        page_params["per_page"] = str(PER_PAGE)
        page_params["page"] = str(page)

        response = await client.get(url, headers=headers, params=page_params)
        if response.status_code != 200:
            return None, None, f"GitHub API returned {response.status_code}: {response.text}"

        data = response.json()
        if total_count is None and isinstance(data.get("total_count"), int):
            total_count = data["total_count"]

        page_items = data.get(item_key, [])
        if not isinstance(page_items, list):
            return None, None, f"GitHub API response missing {item_key} list"

        items.extend(page_items)
        if len(page_items) < PER_PAGE:
            return items, total_count, None

        page += 1


def get_pr_check_runs(pull_number: int) -> dict[str, Any]:
    """Get CI check run status for a pull request.

    Returns all check runs for the PR's latest commit with their status and conclusion.
    Use this to check if CI is passing before declaring a PR ready for review.

    Note: this returns all check runs (GitHub Actions plus any third-party CI like
    CircleCI, Vercel, etc.). The companion `rerun_failed_workflow_runs` tool only
    retries GitHub Actions workflow runs.

    Args:
        pull_number: The PR number to get check runs for.

    Returns:
        Dictionary with success status and check run summary per check.
    """
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    async def _fetch() -> dict[str, Any]:
        async with _http_client() as client:
            # Step 1: get the PR's head commit SHA
            pr_url = f"{_repo_url(repo_config)}/pulls/{pull_number}"
            pr_response = await client.get(pr_url, headers=_github_headers(token))
            if pr_response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {pr_response.status_code} fetching PR: {pr_response.text}",
                }
            head_sha = pr_response.json().get("head", {}).get("sha")
            if not head_sha:
                return {"success": False, "error": "Could not determine head SHA for PR"}

            # Step 2: get check runs for that SHA
            check_runs_url = f"{_repo_url(repo_config)}/commits/{head_sha}/check-runs"
            check_runs, total_count, error = await _fetch_paginated_items(
                client,
                check_runs_url,
                _github_headers(token),
                "check_runs",
            )
            if error or check_runs is None:
                return {
                    "success": False,
                    "error": f"Error fetching check runs: {error}",
                }

            summary = [
                {
                    "id": run.get("id"),
                    "name": run.get("name"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "html_url": run.get("html_url"),
                }
                for run in check_runs
            ]

            has_check_runs = bool(check_runs)
            all_passed = all(
                run.get("conclusion") == "success"
                for run in check_runs
                if run.get("status") == "completed"
            )
            any_failed = any(run.get("conclusion") in FAILED_CONCLUSIONS for run in check_runs)
            any_pending = any(run.get("status") != "completed" for run in check_runs)

            return {
                "success": True,
                "head_sha": head_sha,
                "total_count": total_count if total_count is not None else len(check_runs),
                "check_runs": summary,
                "all_passed": has_check_runs and all_passed and not any_pending,
                "any_failed": any_failed,
                "any_pending": any_pending,
            }

    return asyncio.run(_fetch())


def rerun_failed_workflow_runs(pull_number: int) -> dict[str, Any]:
    """Rerun failed jobs for failed/timed-out/cancelled GitHub Actions workflow runs.

    Use this to retry flaky CI failures without human intervention. Only operates on
    GitHub Actions workflow runs — third-party CI checks (CircleCI, Vercel, etc.)
    surfaced by `get_pr_check_runs` are not affected. Skips runs with conclusion
    `action_required`, since those need manual approval (e.g. environment protection)
    rather than a rerun.

    Args:
        pull_number: The PR number whose failed CI runs should be rerun.

    Returns:
        Dictionary with success status and details of which runs were rerun.
    """
    repo_config = _get_repo_config()
    if not repo_config:
        return {"success": False, "error": "No repo config found"}

    token = asyncio.run(_get_token())
    if not token:
        return {"success": False, "error": "Failed to get GitHub App installation token"}

    async def _rerun() -> dict[str, Any]:
        async with _http_client() as client:
            # Step 1: get the PR's head commit SHA
            pr_url = f"{_repo_url(repo_config)}/pulls/{pull_number}"
            pr_response = await client.get(pr_url, headers=_github_headers(token))
            if pr_response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {pr_response.status_code} fetching PR: {pr_response.text}",
                }
            head_sha = pr_response.json().get("head", {}).get("sha")
            if not head_sha:
                return {"success": False, "error": "Could not determine head SHA for PR"}

            # Step 2: get workflow runs for that SHA
            runs_url = f"{_repo_url(repo_config)}/actions/runs"
            workflow_runs, _, error = await _fetch_paginated_items(
                client,
                runs_url,
                _github_headers(token),
                "workflow_runs",
                params={"head_sha": head_sha},
            )
            if error or workflow_runs is None:
                return {
                    "success": False,
                    "error": f"Error fetching workflow runs: {error}",
                }

            failed_run_ids = [
                run["id"]
                for run in workflow_runs
                if run.get("conclusion") in RERUNNABLE_CONCLUSIONS
            ]

            if not failed_run_ids:
                return {
                    "success": True,
                    "message": "No failed workflow runs found for the PR's latest commit",
                    "head_sha": head_sha,
                    "rerun_run_ids": [],
                }

            # Step 3: rerun failed jobs concurrently for all failed workflow runs
            async def _rerun_one(run_id: int) -> dict[str, Any]:
                rerun_url = f"{_repo_url(repo_config)}/actions/runs/{run_id}/rerun-failed-jobs"
                rerun_response = await client.post(rerun_url, headers=_github_headers(token))
                return {
                    "run_id": run_id,
                    "status_code": rerun_response.status_code,
                    "success": rerun_response.status_code in (200, 201, 204),
                }

            rerun_results = await asyncio.gather(*(_rerun_one(rid) for rid in failed_run_ids))

            all_rerun_succeeded = all(r["success"] for r in rerun_results)
            return {
                "success": all_rerun_succeeded,
                "head_sha": head_sha,
                "rerun_run_ids": failed_run_ids,
                "rerun_results": list(rerun_results),
            }

    return asyncio.run(_rerun())
