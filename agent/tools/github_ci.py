import asyncio
from typing import Any

import httpx
from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token

GITHUB_API_BASE = "https://api.github.com"


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


def get_pr_check_runs(pull_number: int) -> dict[str, Any]:
    """Get CI check run status for a pull request.

    Returns all check runs for the PR's latest commit with their status and conclusion.
    Use this to check if CI is passing before declaring a PR ready for review.

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
        async with httpx.AsyncClient() as client:
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
            cr_response = await client.get(check_runs_url, headers=_github_headers(token))
            if cr_response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {cr_response.status_code} fetching check runs: {cr_response.text}",
                }

            data = cr_response.json()
            check_runs = data.get("check_runs", [])
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

            all_passed = all(
                run.get("conclusion") == "success"
                for run in check_runs
                if run.get("status") == "completed"
            )
            any_failed = any(
                run.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")
                for run in check_runs
            )
            any_pending = any(run.get("status") != "completed" for run in check_runs)

            return {
                "success": True,
                "head_sha": head_sha,
                "total_count": data.get("total_count", len(check_runs)),
                "check_runs": summary,
                "all_passed": all_passed and not any_pending,
                "any_failed": any_failed,
                "any_pending": any_pending,
            }

    return asyncio.run(_fetch())


def rerun_failed_check_runs(pull_number: int) -> dict[str, Any]:
    """Rerun failed or cancelled GitHub Actions check runs for a pull request.

    Use this to retry flaky CI failures without human intervention.
    Finds all failed workflow runs associated with the PR's latest commit and
    reruns only the failed jobs.

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
        async with httpx.AsyncClient() as client:
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
            runs_response = await client.get(
                runs_url,
                headers=_github_headers(token),
                params={"head_sha": head_sha},
            )
            if runs_response.status_code != 200:
                return {
                    "success": False,
                    "error": f"GitHub API returned {runs_response.status_code} fetching workflow runs: {runs_response.text}",
                }

            workflow_runs = runs_response.json().get("workflow_runs", [])
            failed_run_ids = [
                run["id"]
                for run in workflow_runs
                if run.get("conclusion") in ("failure", "timed_out", "cancelled", "action_required")
            ]

            if not failed_run_ids:
                return {
                    "success": True,
                    "message": "No failed workflow runs found for the PR's latest commit",
                    "head_sha": head_sha,
                    "rerun_run_ids": [],
                }

            # Step 3: rerun failed jobs for each failed workflow run
            rerun_results = []
            for run_id in failed_run_ids:
                rerun_url = f"{_repo_url(repo_config)}/actions/runs/{run_id}/rerun-failed-jobs"
                rerun_response = await client.post(rerun_url, headers=_github_headers(token))
                rerun_results.append(
                    {
                        "run_id": run_id,
                        "status_code": rerun_response.status_code,
                        "success": rerun_response.status_code in (200, 201, 204),
                    }
                )

            all_rerun_succeeded = all(r["success"] for r in rerun_results)
            return {
                "success": all_rerun_succeeded,
                "head_sha": head_sha,
                "rerun_run_ids": failed_run_ids,
                "rerun_results": rerun_results,
            }

    return asyncio.run(_rerun())
