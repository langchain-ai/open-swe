"""Take a draft pull request out of draft using the signed-in user's GitHub permissions."""

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel

from agent.github.http import GITHUB_GRAPHQL, github_client, github_request
from agent.github.pull_request_status import pull_request_identity

# REST cannot clear the draft flag; the mutation is the only way, and it needs a node id.
_NODE_ID_QUERY = """
query PullRequestReadyNodeId($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      isDraft
    }
  }
}
"""

_READY_MUTATION = """
mutation MarkPullRequestReady($pullRequestId: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $pullRequestId}) {
    pullRequest {
      isDraft
    }
  }
}
"""

_TRANSPORT_FAILURE = "Could not confirm the change. Refresh to check the PR before retrying."
_INVALID_RESPONSE = "GitHub returned an invalid response. Refresh to check the PR."
_UNCONFIRMED = "GitHub did not confirm the pull request is ready for review."


class ReadyPullRequestResult(BaseModel):
    ready: bool


def _error_message(payload: dict[str, object]) -> str | None:
    errors = payload.get("errors")
    if isinstance(errors, list):
        for error in errors:
            message = error.get("message") if isinstance(error, dict) else None
            if isinstance(message, str) and message:
                return message
    message = payload.get("message")
    return message if isinstance(message, str) and message else None


async def _graphql(
    client: httpx2.AsyncClient, query: str, variables: dict[str, object]
) -> dict[str, object]:
    try:
        response = await github_request(
            client,
            "POST",
            GITHUB_GRAPHQL,
            json={"query": query, "variables": variables},
            max_retries=0,
        )
    except httpx2.HTTPError as exc:
        raise HTTPException(502, _TRANSPORT_FAILURE) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(502, _INVALID_RESPONSE) from exc
    if not isinstance(payload, dict):
        raise HTTPException(502, _INVALID_RESPONSE)
    message = _error_message(payload)
    if message is not None or not response.is_success:
        raise HTTPException(
            response.status_code if 400 <= response.status_code < 500 else 502,
            message or _UNCONFIRMED,
        )
    return payload


def _node(payload: dict[str, object], *keys: str) -> dict[str, object] | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, dict) else None


async def mark_pull_request_ready(
    owner: str, repo: str, number: int, token: str
) -> ReadyPullRequestResult:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    async with github_client(token=token) as client:
        lookup = await _graphql(
            client, _NODE_ID_QUERY, {"owner": owner, "repo": repo, "number": number}
        )
        pull = _node(lookup, "data", "repository", "pullRequest")
        node_id = pull.get("id") if pull is not None else None
        if not isinstance(node_id, str) or not node_id:
            raise HTTPException(502, _INVALID_RESPONSE)
        if pull is not None and pull.get("isDraft") is False:
            return ReadyPullRequestResult(ready=True)
        mutation = await _graphql(client, _READY_MUTATION, {"pullRequestId": node_id})
    ready = _node(mutation, "data", "markPullRequestReadyForReview", "pullRequest")
    if ready is None or ready.get("isDraft") is not False:
        raise HTTPException(502, _UNCONFIRMED)
    return ReadyPullRequestResult(ready=True)
