"""Close a pull request using the signed-in user's GitHub permissions."""

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel

from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.pull_request_status import pull_request_identity


class ClosePullRequestResult(BaseModel):
    closed: bool


async def close_pull_request(
    owner: str, repo: str, number: int, token: str
) -> ClosePullRequestResult:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    try:
        async with github_client(token=token) as client:
            response = await github_request(
                client,
                "PATCH",
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}",
                json={"state": "closed"},
                max_retries=0,
            )
    except httpx2.HTTPError as exc:
        raise HTTPException(
            502, "Could not confirm close. Refresh to check the PR before retrying."
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            502, "GitHub returned an invalid close response. Refresh to check the PR."
        ) from exc
    if not response.is_success or not isinstance(payload, dict) or payload.get("state") != "closed":
        message = payload.get("message") if isinstance(payload, dict) else None
        raise HTTPException(
            response.status_code if 400 <= response.status_code < 500 else 502,
            message if isinstance(message, str) else "GitHub did not confirm the close.",
        )
    return ClosePullRequestResult(closed=True)
