"""Act on a pull request using the signed-in user's GitHub permissions."""

import logging
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
from agent.github.pull_request_status import (
    fetch_unresolved_review_threads,
    pull_request_identity,
)
from agent.github.repo_merge_methods import MergeMethod

logger = logging.getLogger(__name__)

PullRequestActionName = Literal["merge", "close", "mark-ready"]
_COMMENTS_PER_PAGE = 100

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
    reason: str = Field(default="", max_length=65_536)

    transport_failure: ClassVar[str] = (
        "Could not confirm close. Refresh to check the PR before retrying."
    )
    invalid_response: ClassVar[str] = (
        "GitHub returned an invalid close response. Refresh to check the PR."
    )
    unconfirmed: ClassVar[str] = "GitHub did not confirm the close."

    async def _latest_comment_body(
        self, client: httpx2.AsyncClient, owner: str, repo: str, number: int
    ) -> str | None:
        issue_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}"
        response, issue = await self._request(client, "GET", issue_url)
        if not response.is_success:
            raise self._refusal(response, issue)
        count = issue.get("comments")
        if not isinstance(count, int) or count < 1:
            return None
        last_page = (count + _COMMENTS_PER_PAGE - 1) // _COMMENTS_PER_PAGE
        try:
            listed = await github_request(
                client,
                "GET",
                f"{issue_url}/comments?per_page={_COMMENTS_PER_PAGE}&page={last_page}",
                max_retries=0,
            )
            comments = listed.json()
        except (httpx2.HTTPError, ValueError) as exc:
            raise HTTPException(502, self.transport_failure) from exc
        if not listed.is_success or not isinstance(comments, list) or not comments:
            logger.warning(
                "Could not read the latest pull request comment",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            )
            raise HTTPException(502, self.transport_failure)
        last = comments[-1]
        body = last.get("body") if isinstance(last, dict) else None
        return body.strip() if isinstance(body, str) else None

    async def perform(self, client: httpx2.AsyncClient, owner: str, repo: str, number: int) -> None:
        reason = self.reason.strip()
        # A retry after a failed close must not post the same reason twice.
        if reason and await self._latest_comment_body(client, owner, repo, number) != reason:
            response, payload = await self._request(
                client,
                "POST",
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}/comments",
                {"body": reason},
            )
            if not response.is_success:
                raise self._refusal(response, payload)
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


PullRequestAction = Annotated[
    MergeAction | CloseAction | MarkReadyAction, Field(discriminator="action")
]


_RESOLVE_THREAD_MUTATION = """
mutation ResolveReviewThread($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread {
      isResolved
    }
  }
}
"""


class ResolveReviewThreads(BaseModel):
    thread_ids: list[str] = Field(min_length=1, max_length=500)


class ResolveReviewThreadsResult(BaseModel):
    resolved: list[str]
    failed: list[str]


async def _resolve_review_thread(client: httpx2.AsyncClient, thread_id: str) -> bool:
    try:
        response = await github_request(
            client,
            "POST",
            GITHUB_GRAPHQL,
            json={"query": _RESOLVE_THREAD_MUTATION, "variables": {"threadId": thread_id}},
        )
        payload = response.json()
    except httpx2.HTTPError, ValueError:
        logger.warning(
            "Failed to resolve review thread",
            extra={"review_thread_id": thread_id},
            exc_info=True,
        )
        return False
    if not isinstance(payload, dict):
        logger.warning("Unexpected resolve response", extra={"review_thread_id": thread_id})
        return False
    thread = _node(payload, "data", "resolveReviewThread", "thread")
    if thread is None or thread.get("isResolved") is not True:
        logger.warning(
            "GitHub did not resolve review thread",
            extra={
                "review_thread_id": thread_id,
                "github_error": _graphql_error_message(payload),
            },
        )
        return False
    return True


async def resolve_review_threads(
    owner: str, repo: str, number: int, body: ResolveReviewThreads, token: str
) -> ResolveReviewThreadsResult:
    """Resolve review threads as the signed-in user, limited to this PR's open threads."""
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    async with github_client(token=token) as client:
        threads = await fetch_unresolved_review_threads(client, owner, repo, number)
        if threads is None:
            logger.warning(
                "Review threads unavailable for resolve",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": number},
            )
            raise HTTPException(502, "GitHub did not return the review threads")
        open_ids = {thread["thread_id"] for thread in threads if thread.get("thread_id")}
        resolved: list[str] = []
        failed: list[str] = []
        # Serial on purpose: GitHub's secondary rate limit punishes concurrent mutations.
        for thread_id in dict.fromkeys(body.thread_ids):
            if thread_id not in open_ids:
                continue
            (resolved if await _resolve_review_thread(client, thread_id) else failed).append(
                thread_id
            )
    return ResolveReviewThreadsResult(resolved=resolved, failed=failed)


async def act_on_pull_request(
    owner: str, repo: str, number: int, action: PullRequestAction, token: str
) -> PullRequestActionResult:
    if pull_request_identity({"repo_full_name": f"{owner}/{repo}", "number": number}) is None:
        raise HTTPException(422, "invalid pull request")
    async with github_client(token=token) as client:
        try:
            await action.perform(client, owner, repo, number)
        except HTTPException as exc:
            logger.warning(
                "Pull request action failed",
                extra={
                    "pr_repo_full_name": f"{owner}/{repo}",
                    "pr_number": number,
                    "pr_action": action.action,
                    "status_code": exc.status_code,
                    "error_detail": exc.detail,
                },
                exc_info=exc.__cause__ is not None,
            )
            raise
    return PullRequestActionResult(action=action.action, done=True)
