"""Act on a pull request using the signed-in user's GitHub permissions."""

from typing import Annotated, ClassVar, Literal

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, Field

from agent.github.http import (
    GITHUB_API_BASE,
    GITHUB_GRAPHQL,
    github_client,
    github_request,
)
from agent.github.pull_request_status import pull_request_identity
from agent.github.repo_merge_methods import MergeMethod

PullRequestActionName = Literal["merge", "close", "mark-ready", "approve"]

# REST cannot clear the draft flag, so marking a PR ready has to go through GraphQL.
_READY_MUTATION = """
mutation MarkPullRequestReady($pullRequestId: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $pullRequestId}) {
    pullRequest {
      isDraft
    }
  }
}
"""


class PullRequestActionResult(BaseModel):
    action: PullRequestActionName
    done: bool


def _graphql_error_message(payload: dict[str, object]) -> str | None:
    errors = payload.get("errors")
    if isinstance(errors, list):
        for error in errors:
            if not isinstance(error, dict):
                continue
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
    return None


def _node(payload: dict[str, object], *keys: str) -> dict[str, object] | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, dict) else None


class _PullRequestActionBase(BaseModel):
    transport_failure: ClassVar[str]
    invalid_response: ClassVar[str]
    unconfirmed: ClassVar[str]

    async def perform(self, client: httpx2.AsyncClient, owner: str, repo: str, number: int) -> None:
        raise NotImplementedError

    async def _request(
        self,
        client: httpx2.AsyncClient,
        method: str,
        url: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[httpx2.Response, dict[str, object]]:
        body: dict[str, object] = {} if payload is None else {"json": payload}
        try:
            response = await github_request(client, method, url, max_retries=0, **body)
        except httpx2.HTTPError as exc:
            raise HTTPException(502, self.transport_failure) from exc
        try:
            parsed = response.json()
        except ValueError as exc:
            raise HTTPException(502, self.invalid_response) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(502, self.invalid_response)
        return response, parsed

    def _refusal(self, response: httpx2.Response, payload: dict[str, object]) -> HTTPException:
        message = payload.get("message")
        return HTTPException(
            response.status_code if 400 <= response.status_code < 500 else 502,
            message if isinstance(message, str) and message else self.unconfirmed,
        )

    async def _confirm(
        self,
        client: httpx2.AsyncClient,
        method: str,
        url: str,
        payload: dict[str, object],
        field: str,
        expected: object,
    ) -> None:
        response, parsed = await self._request(client, method, url, payload)
        if not response.is_success or parsed.get(field) != expected:
            raise self._refusal(response, parsed)


class MergeAction(_PullRequestActionBase):
    action: Literal["merge"]
    sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    merge_method: MergeMethod

    transport_failure: ClassVar[str] = (
        "Could not confirm merge. Refresh to check the PR before retrying."
    )
    invalid_response: ClassVar[str] = (
        "GitHub returned an invalid merge response. Refresh to check the PR."
    )
    unconfirmed: ClassVar[str] = "GitHub did not confirm the merge."

    async def perform(self, client: httpx2.AsyncClient, owner: str, repo: str, number: int) -> None:
        await self._confirm(
            client,
            "PUT",
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/merge",
            {"sha": self.sha, "merge_method": self.merge_method},
            "merged",
            True,
        )


class CloseAction(_PullRequestActionBase):
    action: Literal["close"]

    transport_failure: ClassVar[str] = (
        "Could not confirm close. Refresh to check the PR before retrying."
    )
    invalid_response: ClassVar[str] = (
        "GitHub returned an invalid close response. Refresh to check the PR."
    )
    unconfirmed: ClassVar[str] = "GitHub did not confirm the close."

    async def perform(self, client: httpx2.AsyncClient, owner: str, repo: str, number: int) -> None:
        await self._confirm(
            client,
            "PATCH",
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}",
            {"state": "closed"},
            "state",
            "closed",
        )


class MarkReadyAction(_PullRequestActionBase):
    action: Literal["mark-ready"]

    transport_failure: ClassVar[str] = (
        "Could not confirm the change. Refresh to check the PR before retrying."
    )
    invalid_response: ClassVar[str] = (
        "GitHub returned an invalid response. Refresh to check the PR."
    )
    unconfirmed: ClassVar[str] = "GitHub did not confirm the pull request is ready for review."

    async def perform(self, client: httpx2.AsyncClient, owner: str, repo: str, number: int) -> None:
        response, pull = await self._request(
            client, "GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
        )
        if not response.is_success:
            raise self._refusal(response, pull)
        node_id = pull.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise HTTPException(502, self.invalid_response)
        if pull.get("draft") is False:
            return
        await self._mark_ready(client, node_id)

    async def _mark_ready(self, client: httpx2.AsyncClient, node_id: str) -> None:
        response, payload = await self._request(
            client,
            "POST",
            GITHUB_GRAPHQL,
            {"query": _READY_MUTATION, "variables": {"pullRequestId": node_id}},
        )
        message = _graphql_error_message(payload)
        if message is not None or not response.is_success:
            raise HTTPException(
                response.status_code if 400 <= response.status_code < 500 else 502,
                message or self.unconfirmed,
            )
        ready = _node(payload, "data", "markPullRequestReadyForReview", "pullRequest")
        if ready is None or ready.get("isDraft") is not False:
            raise HTTPException(502, self.unconfirmed)


class ApproveAction(_PullRequestActionBase):
    action: Literal["approve"]
    sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")

    transport_failure: ClassVar[str] = (
        "Could not confirm approval. Refresh to check the PR before retrying."
    )
    invalid_response: ClassVar[str] = (
        "GitHub returned an invalid approval response. Refresh to check the PR."
    )
    unconfirmed: ClassVar[str] = "GitHub did not confirm the approval."

    async def perform(self, client: httpx2.AsyncClient, owner: str, repo: str, number: int) -> None:
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}"
        response, pull = await self._request(client, "GET", url)
        if not response.is_success:
            raise self._refusal(response, pull)
        if pull.get("state") != "open":
            raise HTTPException(409, "Pull request is not open.")
        if pull.get("draft") is not False:
            raise HTTPException(409, "Pull request is still a draft.")
        head = _node(pull, "head")
        head_sha = head.get("sha") if head is not None else None
        if not isinstance(head_sha, str):
            raise HTTPException(502, self.invalid_response)
        if head_sha.lower() != self.sha.lower():
            raise HTTPException(409, "Pull request head changed. Refresh before approving.")
        response, review = await self._request(
            client,
            "POST",
            f"{url}/reviews",
            {
                "commit_id": head_sha,
                "event": "APPROVE",
                "body": "Approved via Open SWE review chat.",
            },
        )
        if (
            not response.is_success
            or review.get("state") != "APPROVED"
            or review.get("commit_id") != head_sha
        ):
            raise self._refusal(response, review)


PullRequestAction = Annotated[
    MergeAction | CloseAction | MarkReadyAction | ApproveAction,
    Field(discriminator="action"),
]


async def act_on_pull_request(
    owner: str, repo: str, number: int, action: PullRequestAction, token: str
) -> PullRequestActionResult:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    async with github_client(token=token) as client:
        await action.perform(client, owner, repo, number)
    return PullRequestActionResult(action=action.action, done=True)
