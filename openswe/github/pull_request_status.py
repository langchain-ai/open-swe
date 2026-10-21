"""One pull request's calls on GitHub, read live.

The dashboard reads pull requests from the mirror (``openswe.github.mirror``);
these are for keeping it current and for reads that gate a write.
"""

import logging
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import quote

import httpx2

from openswe.github.http import GITHUB_API_BASE, GitHubClient, GraphQLError, RepoClient

logger = logging.getLogger(__name__)

_OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPO_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
_MERGEABILITY_QUERY = """
query PullRequestMergeability($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      mergeable
      mergeStateStatus
    }
  }
}
"""
_REVIEW_THREADS_QUERY = """
query PullRequestReviewThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          path
          line
          originalLine
          comments(first: 50) {
            nodes {
              author { login }
              body: bodyText
              url
            }
          }
        }
      }
    }
  }
}
"""
_ALL_REVIEW_THREADS_QUERY = """
query PullRequestAllReviewThreads($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line startLine diffSide startDiffSide
          comments(first: 100) {
            nodes { id fullDatabaseId body pullRequestReview { fullDatabaseId } }
          }
        }
      }
    }
  }
}
"""
_ADD_REVIEW_THREAD_MUTATION = """
mutation($input: AddPullRequestReviewThreadInput!) {
  addPullRequestReviewThread(input: $input) { thread { id } }
}
"""
_RESOLVE_THREAD_MUTATION = """
mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""
_UNRESOLVE_THREAD_MUTATION = """
mutation($id: ID!) { unresolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }
"""
_UPDATE_REVIEW_COMMENT_MUTATION = """
mutation($input: UpdatePullRequestReviewCommentInput!) {
  updatePullRequestReviewComment(input: $input) { pullRequestReviewComment { id } }
}
"""
_REVIEW_DECISION_QUERY = """
query PullRequestReviewDecision($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) { reviewDecision }
  }
}
"""


def pull_request_identity(record: object) -> tuple[str, str, int] | None:
    if not isinstance(record, Mapping):
        return None
    full_name = record.get("repo_full_name")
    number = record.get("number")
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        return None
    owner, repo = full_name.split("/", 1)
    if (
        not _OWNER_PATTERN.fullmatch(owner)
        or not _REPO_PATTERN.fullmatch(repo)
        or repo in {".", ".."}
    ):
        return None
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        return None
    return owner, repo, number


def _thread_reply(comment: dict[str, Any]) -> dict[str, Any]:
    author = comment.get("author")
    return {
        "author": author.get("login")
        if isinstance(author, dict) and isinstance(author.get("login"), str)
        else None,
        "body": comment.get("body") if isinstance(comment.get("body"), str) else "",
        "url": comment.get("url") if isinstance(comment.get("url"), str) else None,
    }


@dataclass(frozen=True, slots=True)
class Mergeability:
    """GitHub's verdict on whether a pull request can merge."""

    mergeable: bool | None
    merge_state: str


class _CursorLoop(ValueError):
    """GitHub handed back a page cursor it had already given."""


def _graphql_pull(data: Mapping[str, Any]) -> Mapping[str, Any] | None:
    repository = data.get("repository")
    pull = repository.get("pullRequest") if isinstance(repository, Mapping) else None
    return pull if isinstance(pull, Mapping) else None


def _next_cursor(connection: Mapping[str, Any], seen: set[str]) -> str | None:
    """The cursor of the next page, ``None`` on the last page."""
    page_info = connection.get("pageInfo")
    if not isinstance(page_info, Mapping) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor or cursor in seen:
        raise _CursorLoop
    seen.add(cursor)
    return cursor


@dataclass(frozen=True, slots=True)
class PullRequestClient:
    """One pull request's calls on GitHub; plain reads and writes raise like ``RepoClient``.

    The status reads (``mergeability``, ``unresolved_threads``) answer ``None``
    instead, so one failed read degrades its part of a status rather than the
    whole of it.
    """

    repo: RepoClient
    number: int

    @classmethod
    @asynccontextmanager
    async def as_app(cls, owner: str, repo: str, number: int) -> AsyncIterator[Self]:
        """This pull request as the Open SWE GitHub App's installation on ``owner/repo`` reads it.

        Raises ``GitHubAppUnavailable`` when no installation token can be minted.
        """
        async with GitHubClient.as_app(owner, repo) as github:
            yield cls(github.repo(owner, repo), number)

    @classmethod
    def of(cls, github: GitHubClient, record: object) -> Self | None:
        """The client for a ``repo_full_name`` and ``number``, or for a search hit's ``repository_url``."""
        if not isinstance(record, Mapping):
            return None
        full_name = record.get("repo_full_name")
        repository_url = record.get("repository_url")
        prefix = f"{GITHUB_API_BASE}/repos/"
        if not isinstance(full_name, str):
            full_name = (
                repository_url.removeprefix(prefix)
                if isinstance(repository_url, str) and repository_url.startswith(prefix)
                else ""
            )
        identity = pull_request_identity(
            {"repo_full_name": full_name, "number": record.get("number")}
        )
        if identity is None:
            return None
        owner, name, number = identity
        return cls(github.repo(owner, name), number)

    @property
    def _log_extra(self) -> dict[str, object]:
        return {"pr_repo_full_name": self.repo.full_name, "pr_number": self.number}

    async def pull(self) -> dict[str, Any]:
        payload = await self.repo.get(f"pulls/{self.number}")
        if not isinstance(payload, dict):
            raise ValueError("GitHub answered the pull request without an object")
        return payload

    async def head_sha(self) -> str:
        head = (await self.pull()).get("head")
        sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(sha, str) or not sha:
            raise ValueError("GitHub answered the pull request without a head commit")
        return sha

    async def files(self, *, max_pages: int = 1) -> list[dict[str, Any]]:
        """Changed files, up to ``max_pages`` pages of 100."""
        return await self.repo.pages(f"pulls/{self.number}/files", max_pages=max_pages)

    async def issue_comments(self) -> list[dict[str, Any]]:
        """Top-level conversation comments."""
        return await self.repo.pages(f"issues/{self.number}/comments")

    async def comment(self, body: str) -> object:
        """Add a top-level conversation comment."""
        return await self.repo.post(f"issues/{self.number}/comments", {"body": body})

    async def labels(self) -> list[dict[str, Any]]:
        return await self.repo.pages(f"issues/{self.number}/labels")

    async def add_label(self, name: str) -> None:
        await self.repo.post(f"issues/{self.number}/labels", {"labels": [name]})

    async def remove_label(self, name: str) -> None:
        await self.repo.delete(f"issues/{self.number}/labels/{quote(name, safe='')}")

    async def review_comments(
        self, *, newest_first: bool = False, max_pages: int | None = None
    ) -> list[dict[str, Any]]:
        """Inline review comments, oldest first unless ``newest_first``."""
        params = {"sort": "created", "direction": "desc"} if newest_first else None
        return await self.repo.pages(
            f"pulls/{self.number}/comments", params=params, max_pages=max_pages
        )

    async def reply_to_review_comment(self, comment_id: int, body: str) -> object:
        """Reply in the inline thread that ``comment_id`` opened."""
        return await self.repo.post(
            f"pulls/{self.number}/comments/{comment_id}/replies", {"body": body}
        )

    async def commits(self) -> list[dict[str, Any]]:
        return await self.repo.pages(f"pulls/{self.number}/commits")

    async def add_review_comment(self, comment: Mapping[str, object]) -> object:
        """Post one inline comment now, outside any review."""
        return await self.repo.post(f"pulls/{self.number}/comments", comment)

    async def create_review(self, review: Mapping[str, object]) -> object:
        """Start a review; without an ``event`` it stays pending for its author."""
        return await self.repo.post(f"pulls/{self.number}/reviews", review)

    async def submit_review(self, review_id: int, *, event: str, body: str) -> object:
        payload: dict[str, object] = {"event": event}
        if body:
            payload["body"] = body
        return await self.repo.post(f"pulls/{self.number}/reviews/{review_id}/events", payload)

    async def delete_review(self, review_id: int) -> None:
        await self.repo.delete(f"pulls/{self.number}/reviews/{review_id}")

    async def review_threads(self) -> list[dict[str, Any]]:
        """Every review thread with its position and comments, pending ones included."""
        threads: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            pull = _graphql_pull(
                await self.repo.graphql(
                    _ALL_REVIEW_THREADS_QUERY, {"number": self.number, "cursor": cursor}
                )
            )
            connection = pull.get("reviewThreads") if pull is not None else None
            if not isinstance(connection, Mapping) or not isinstance(connection.get("nodes"), list):
                raise ValueError("GitHub answered review threads without a list")
            threads.extend(node for node in connection["nodes"] if isinstance(node, dict))
            cursor = _next_cursor(connection, seen)
            if cursor is None:
                return threads

    async def add_review_thread(self, thread: Mapping[str, object]) -> None:
        """Add an inline comment to a pending review; ``thread`` is GraphQL's thread input."""
        await self.repo.github.graphql(_ADD_REVIEW_THREAD_MUTATION, {"input": dict(thread)})

    async def set_thread_resolved(self, thread_node_id: str, resolved: bool) -> None:
        await self.repo.github.graphql(
            _RESOLVE_THREAD_MUTATION if resolved else _UNRESOLVE_THREAD_MUTATION,
            {"id": thread_node_id},
        )

    async def edit_pending_comment(self, comment_node_id: str, body: str) -> None:
        """Edit a comment in a pending review, which REST cannot reach yet."""
        await self.repo.github.graphql(
            _UPDATE_REVIEW_COMMENT_MUTATION,
            {"input": {"pullRequestReviewCommentId": comment_node_id, "body": body}},
        )

    async def dismiss_review(self, review_id: int, message: str) -> None:
        await self.repo.github.request(
            "PUT",
            f"repos/{self.repo.full_name}/pulls/{self.number}/reviews/{review_id}/dismissals",
            json={"message": message, "event": "DISMISS"},
        )

    async def request_reviewers(self, logins: list[str]) -> None:
        await self.repo.post(f"pulls/{self.number}/requested_reviewers", {"reviewers": logins})

    async def remove_requested_reviewers(self, logins: list[str]) -> None:
        await self.repo.delete(f"pulls/{self.number}/requested_reviewers", {"reviewers": logins})

    async def merge(self, *, sha: str, method: str, commit_message: str | None = None) -> None:
        """Merge ``sha`` with ``method``; GitHub refusing it raises ``GitHubError``."""
        payload: dict[str, object] = {"sha": sha, "merge_method": method}
        if commit_message is not None:
            payload["commit_message"] = commit_message
        await self.repo.github.request(
            "PUT", f"repos/{self.repo.full_name}/pulls/{self.number}/merge", json=payload
        )

    async def reviews(self) -> list[dict[str, Any]]:
        return await self.repo.pages(f"pulls/{self.number}/reviews")

    async def mergeability(self) -> Mergeability | None:
        """Mergeability over GraphQL.

        REST answers ``mergeable: null`` whenever its cached verdict has expired,
        and only starts recomputing it; GraphQL waits for that computation, so a
        single read usually gets the real answer.
        """
        try:
            data = await self.repo.graphql(_MERGEABILITY_QUERY, {"number": self.number})
        except GraphQLError as exc:
            logger.warning(
                "Mergeability query answered with errors; falling back to what REST reported",
                extra={**self._log_extra, "graphql_errors": exc.errors},
            )
            return None
        except httpx2.HTTPError, ValueError:
            logger.warning(
                "Mergeability query failed; falling back to what REST reported",
                extra=self._log_extra,
                exc_info=True,
            )
            return None
        pull = _graphql_pull(data)
        if pull is None:
            logger.warning(
                "Mergeability query answered without a pull request", extra=self._log_extra
            )
            return None
        mergeable = pull.get("mergeable")
        merge_state = pull.get("mergeStateStatus")
        return Mergeability(
            mergeable={"MERGEABLE": True, "CONFLICTING": False}.get(
                mergeable if isinstance(mergeable, str) else ""
            ),
            merge_state=merge_state.lower() if isinstance(merge_state, str) else "",
        )

    async def unresolved_threads(self) -> list[dict[str, Any]] | None:
        unresolved: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        try:
            while True:
                data = await self.repo.graphql(
                    _REVIEW_THREADS_QUERY, {"number": self.number, "cursor": cursor}
                )
                pull = _graphql_pull(data)
                threads = pull.get("reviewThreads") if pull is not None else None
                if not isinstance(threads, dict) or not isinstance(threads.get("nodes"), list):
                    return None
                for thread in threads["nodes"]:
                    if not isinstance(thread, dict) or thread.get("isResolved") is True:
                        continue
                    comments = thread.get("comments")
                    nodes = comments.get("nodes") if isinstance(comments, dict) else None
                    comment = (
                        nodes[0]
                        if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict)
                        else {}
                    )
                    author = comment.get("author")
                    line = thread.get("line")
                    if not isinstance(line, int) or isinstance(line, bool):
                        line = thread.get("originalLine")
                    unresolved.append(
                        {
                            "thread_id": thread.get("id")
                            if isinstance(thread.get("id"), str)
                            else None,
                            "author": author.get("login")
                            if isinstance(author, dict) and isinstance(author.get("login"), str)
                            else None,
                            "body": comment.get("body")
                            if isinstance(comment.get("body"), str)
                            else "",
                            "path": thread.get("path")
                            if isinstance(thread.get("path"), str)
                            else "",
                            "line": line
                            if isinstance(line, int) and not isinstance(line, bool)
                            else None,
                            "url": comment.get("url")
                            if isinstance(comment.get("url"), str)
                            else None,
                            "replies": [
                                _thread_reply(reply)
                                for reply in (nodes[1:] if isinstance(nodes, list) else [])
                                if isinstance(reply, dict)
                            ],
                        }
                    )
                cursor = _next_cursor(threads, seen_cursors)
                if cursor is None:
                    return unresolved
        except _CursorLoop, httpx2.HTTPError, ValueError:
            return None

    async def review_decision(self) -> str:
        """``reviewDecision`` lowercased, ``none`` when branch protection asks for no review."""
        pull = _graphql_pull(
            await self.repo.graphql(_REVIEW_DECISION_QUERY, {"number": self.number})
        )
        decision = pull.get("reviewDecision") if pull is not None else None
        return decision.lower() if isinstance(decision, str) else "none"
