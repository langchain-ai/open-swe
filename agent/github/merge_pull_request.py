"""Merge a pull request using the signed-in user's GitHub permissions."""

from typing import Literal

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, Field

from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.pull_request_status import pull_request_identity

MergeMethod = Literal["squash", "merge", "rebase"]

_MERGE_METHOD_FLAGS: tuple[tuple[MergeMethod, str], ...] = (
    ("squash", "allow_squash_merge"),
    ("merge", "allow_merge_commit"),
    ("rebase", "allow_rebase_merge"),
)


class MergePullRequestRequest(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    merge_method: MergeMethod


async def merge_pull_request(
    owner: str, repo: str, number: int, body: MergePullRequestRequest, token: str
) -> dict[str, bool]:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    try:
        async with github_client(token=token) as client:
            response = await github_request(
                client,
                "PUT",
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/merge",
                json=body.model_dump(),
                max_retries=0,
            )
    except httpx2.HTTPError as exc:
        raise HTTPException(
            502, "Could not confirm merge. Refresh to check the PR before retrying."
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            502, "GitHub returned an invalid merge response. Refresh to check the PR."
        ) from exc
    if (
        not response.is_success
        or not isinstance(payload, dict)
        or payload.get("merged") is not True
    ):
        message = payload.get("message") if isinstance(payload, dict) else None
        raise HTTPException(
            response.status_code if 400 <= response.status_code < 500 else 502,
            message if isinstance(message, str) else "GitHub did not confirm the merge.",
        )
    return {"merged": True}


async def repository_merge_methods(owner: str, repo: str, token: str) -> dict[str, list[str]]:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": 1}) is None:
        raise HTTPException(422, "invalid repository")
    try:
        async with github_client(token=token) as client:
            response = await github_request(
                client, "GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
            )
        payload = response.json()
    except (httpx2.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Could not load merge settings from GitHub") from exc
    if not response.is_success or not isinstance(payload, dict):
        raise HTTPException(502, "Could not load merge settings from GitHub")
    methods: list[MergeMethod] = [
        method
        for method, flag in _MERGE_METHOD_FLAGS
        if not isinstance(payload.get(flag), bool) or payload[flag]
    ]
    return {"mergeMethods": list(methods)}
