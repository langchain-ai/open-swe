from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from ..merge_controller import MergeResult
from .github_http import GITHUB_API_BASE, github_client, github_request

_BLOCKED_STATUSES = frozenset({405, 409})


async def merge_pull_request(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    sha: str,
    merge_method: str = "squash",
    commit_title: str | None = None,
    commit_message: str | None = None,
    client_factory: Callable[..., Any] = github_client,
    request_func: Callable[..., Any] = github_request,
) -> MergeResult:
    if not token:
        return MergeResult(success=False, status="blocked", reason="merge_credential_missing")
    if not sha:
        return MergeResult(success=False, status="blocked", reason="missing_sha")

    payload: dict[str, str] = {"sha": sha, "merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title
    if commit_message:
        payload["commit_message"] = commit_message

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/merge"
    try:
        async with client_factory(token=token) as client:
            response = await request_func(client, "PUT", url, json=payload)
    except httpx.HTTPError as exc:
        return MergeResult(
            success=False,
            status="error",
            reason="github_request_failed",
            sha=sha,
            details={"error": str(exc)},
        )

    data = _response_data(response)
    if 200 <= response.status_code < 300 and data.get("merged", True) is not False:
        merged_sha = data.get("sha")
        return MergeResult(
            success=True,
            status="merged",
            reason="merged",
            sha=merged_sha if isinstance(merged_sha, str) and merged_sha else sha,
            http_status=response.status_code,
            details=data,
        )

    if response.status_code in _BLOCKED_STATUSES:
        return MergeResult(
            success=False,
            status="blocked",
            reason="github_merge_blocked",
            sha=sha,
            http_status=response.status_code,
            details=data,
        )

    reason = "github_merge_rejected" if response.status_code == 422 else "github_merge_failed"
    return MergeResult(
        success=False,
        status="error",
        reason=reason,
        sha=sha,
        http_status=response.status_code,
        details=data,
    )


def _response_data(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {"message": response.text}
    if isinstance(data, dict):
        return data
    return {"body": data}
