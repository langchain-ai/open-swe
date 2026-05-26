"""GitHub Reviews API + GraphQL resolveReviewThread for the reviewer agent.

The reviewer agent calls ``publish_review`` at the end of a run. That tool
batches eligible findings (severity ≥ threshold, status=open, capped) into a
single GitHub PR Review:

- Review body: a fixed, host-formatted summary line. The agent never writes
  prose here — it's either "no issues found" or "found N potential issue(s)".
- Inline comments: one per surfaced finding, anchored to ``path`` + ``line``
  (+ ``start_line`` for ranges) + ``side``.
- Suggestion: when ``finding.suggestion`` is set, appended to the comment body
  as a fenced ```suggestion``` block — gives the user the "Commit suggestion"
  button on GitHub.

After publish, the returned per-comment IDs get stored back on each Finding as
``github_review_comment_id``. On a re-review run, when a finding moves
``open`` → ``resolved``, ``resolve_review_thread`` is called for that ID via
the GraphQL ``resolveReviewThread`` mutation (REST doesn't expose this).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .reviewer_findings import Finding
from .utils.github_token import GitHubAuthError

logger = logging.getLogger(__name__)


_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_GRAPHQL = "https://api.github.com/graphql"
_GITHUB_HEADERS_VERSION = "2022-11-28"


def render_inline_comment_body(finding: Finding) -> str:
    """Render the body of one inline review comment.

    Format:

        <description>

        ```suggestion
        <replacement>
        ```

    The suggestion block is only included when ``finding.suggestion`` is set.
    Multi-line suggestions just become multi-line ```suggestion``` blocks.
    """
    description = finding.get("description", "") or ""
    marker_payload = {
        "id": finding.get("id", ""),
        "file_path": finding.get("file", ""),
        "start_line": finding.get("start_line"),
        "end_line": finding.get("end_line"),
        "side": finding.get("side", "RIGHT"),
    }
    marker = f"<!-- open-swe-review-comment {json.dumps(marker_payload, separators=(',', ':'))} -->"
    suggestion = finding.get("suggestion")
    body = f"{marker}\n\n{description}"
    if suggestion:
        body = f"{body}\n\n```suggestion\n{suggestion}\n```"
    return body


def render_inline_comment_payload(finding: Finding) -> dict[str, Any] | None:
    """Render one finding into the payload shape GitHub's Reviews API expects.

    Returns ``None`` for file-level findings (no line range), since the Reviews
    API requires inline comments to be anchored to a line.
    """
    file = finding.get("file")
    start_line = finding.get("start_line")
    end_line = finding.get("end_line")
    side = finding.get("side", "RIGHT")
    if not file or end_line is None:
        return None
    payload: dict[str, Any] = {
        "path": file,
        "line": end_line,
        "side": side,
        "body": render_inline_comment_body(finding),
    }
    if start_line is not None and start_line != end_line:
        payload["start_line"] = start_line
        payload["start_side"] = side
    return payload


def render_review_body(*, pr_number: int, surfaced_count: int) -> str:
    """Compose the top-level review body.

    Two fixed shapes — no agent prose:

    - 0 surfaced findings: a single "no issues" line.
    - N surfaced findings: a single "found N potential issue(s)" line.
    """
    if surfaced_count == 0:
        headline = (
            "## ✅ Open SWE Review: No issues found\n\n"
            "Open SWE reviewed this PR and found no potential bugs to report."
        )
    else:
        issue_word = "issue" if surfaced_count == 1 else "issues"
        headline = f"**Open SWE Review** found {surfaced_count} potential {issue_word}."
    return f"{headline}\n\n<!-- open-swe-reviewer pr={pr_number} -->"


async def post_pull_request_review(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    body: str,
    inline_comments: list[dict[str, Any]],
    token: str,
) -> dict[str, Any] | None:
    """POST one GitHub PR Review with inline comments. Returns the API response or None."""
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    payload: dict[str, Any] = {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": body,
        "comments": inline_comments,
    }
    headers = _github_headers(token)
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 401:
                raise GitHubAuthError(
                    f"GitHub returned 401 posting PR review for {owner}/{repo}#{pr_number}"
                )
            response.raise_for_status()
        except GitHubAuthError:
            raise
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:500]
            logger.exception(
                "Failed to POST PR review for %s/%s#%s: %s %s",
                owner,
                repo,
                pr_number,
                e.response.status_code,
                body,
            )
            return {"_error": f"HTTP {e.response.status_code}: {body}"}
        except httpx.HTTPError as e:
            logger.exception("Failed to POST PR review for %s/%s#%s", owner, repo, pr_number)
            return {"_error": f"{type(e).__name__}: {e}"}
    data = response.json()
    if isinstance(data, dict):
        return data
    body_excerpt = (response.text or "")[:500]
    logger.error(
        "POST PR review for %s/%s#%s returned non-dict body: %s",
        owner,
        repo,
        pr_number,
        body_excerpt,
    )
    return {"_error": (f"HTTP {response.status_code}: non-dict response body: {body_excerpt}")}


async def fetch_review_comments(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    review_id: int,
    token: str,
) -> list[dict[str, Any]]:
    """List the inline comments for a posted review.

    GitHub's review-creation response includes a ``comments`` count but not the
    per-comment IDs in all paths; this paginates the canonical list endpoint.
    """
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews/{review_id}/comments"
    headers = _github_headers(token)
    out: list[dict[str, Any]] = []
    params: dict[str, Any] = {"per_page": 100, "page": 1}
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(url, headers=headers, params=params, timeout=30)
                response.raise_for_status()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to list review comments for review %s on %s/%s",
                    review_id,
                    owner,
                    repo,
                )
                break
            data = response.json()
            if not isinstance(data, list) or not data:
                break
            out.extend(item for item in data if isinstance(item, dict))
            if len(data) < 100:  # noqa: PLR2004
                break
            params["page"] += 1
    return out


async def fetch_pr_review_threads(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    max_threads: int = 100,
    max_comments_per_thread: int = 20,
) -> list[dict[str, Any]]:
    """Fetch all inline review threads on a PR (across reviewers, with replies).

    Returned shape per thread:
        {
            "path": str,
            "line": int | None,
            "original_line": int | None,
            "is_resolved": bool,
            "is_outdated": bool,
            "comments": [{"author": str, "body": str, "created_at": str}, ...],
        }

    Used to give the reviewer agent comment-awareness: it should not re-file a
    finding that already appears as an open thread (its own or another
    reviewer's), and should treat a thread as addressed when a human reply
    explains the code or the thread is resolved.
    """
    query = """
    query Threads($owner: String!, $repo: String!, $pr: Int!, $cursor: String, $perThread: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr) {
          reviewThreads(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              isResolved
              isOutdated
              path
              line
              originalLine
              comments(first: $perThread) {
                nodes {
                  databaseId
                  author { login }
                  authorAssociation
                  body
                  createdAt
                }
              }
            }
          }
        }
      }
    }
    """
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    async with httpx.AsyncClient() as client:
        while len(out) < max_threads:
            try:
                response = await client.post(
                    _GITHUB_GRAPHQL,
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "query": query,
                        "variables": {
                            "owner": owner,
                            "repo": repo,
                            "pr": pr_number,
                            "cursor": cursor,
                            "perThread": max_comments_per_thread,
                        },
                    },
                    timeout=30,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to fetch PR review threads for %s/%s#%s",
                    owner,
                    repo,
                    pr_number,
                )
                return out
            data = response.json()
            threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )
            for thread in threads.get("nodes", []) or []:
                if not isinstance(thread, dict):
                    continue
                comments_block = thread.get("comments") or {}
                comments_nodes = comments_block.get("nodes") or []
                comments: list[dict[str, Any]] = []
                for c in comments_nodes:
                    if not isinstance(c, dict):
                        continue
                    author_block = c.get("author") or {}
                    login = author_block.get("login") if isinstance(author_block, dict) else None
                    comments.append(
                        {
                            "id": c.get("databaseId")
                            if isinstance(c.get("databaseId"), int)
                            else None,
                            "author": login if isinstance(login, str) else "unknown",
                            "author_association": c.get("authorAssociation", "")
                            if isinstance(c.get("authorAssociation"), str)
                            else "",
                            "body": c.get("body", "") if isinstance(c.get("body"), str) else "",
                            "created_at": c.get("createdAt", "")
                            if isinstance(c.get("createdAt"), str)
                            else "",
                        }
                    )
                out.append(
                    {
                        "id": thread.get("id") if isinstance(thread.get("id"), str) else "",
                        "path": thread.get("path", "")
                        if isinstance(thread.get("path"), str)
                        else "",
                        "line": thread.get("line") if isinstance(thread.get("line"), int) else None,
                        "original_line": thread.get("originalLine")
                        if isinstance(thread.get("originalLine"), int)
                        else None,
                        "is_resolved": bool(thread.get("isResolved")),
                        "is_outdated": bool(thread.get("isOutdated")),
                        "comments": comments,
                    }
                )
                if len(out) >= max_threads:
                    break
            page_info = threads.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
    return out


async def fetch_review_thread_id_for_comment(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    review_comment_id: int,
    token: str,
) -> str | None:
    """Resolve the GraphQL review-thread node id for a REST review-comment id.

    GitHub's GraphQL API resolves "threads" rather than individual comments; to
    resolve a thread we need its node id. The REST review-comment id is mapped
    to the thread by walking the PR's review threads.
    """
    query = """
    query Threads($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr) {
          reviewThreads(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              comments(first: 50) { nodes { databaseId } }
            }
          }
        }
      }
    }
    """
    cursor: str | None = None
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.post(
                    _GITHUB_GRAPHQL,
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "query": query,
                        "variables": {
                            "owner": owner,
                            "repo": repo,
                            "pr": pr_number,
                            "cursor": cursor,
                        },
                    },
                    timeout=30,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to fetch review threads for %s/%s#%s",
                    owner,
                    repo,
                    pr_number,
                )
                return None
            data = response.json()
            threads = (
                data.get("data", {})
                .get("repository", {})
                .get("pullRequest", {})
                .get("reviewThreads", {})
            )
            for thread in threads.get("nodes", []) or []:
                comment_ids = {
                    c.get("databaseId") for c in (thread.get("comments", {}).get("nodes") or [])
                }
                if review_comment_id in comment_ids:
                    node_id = thread.get("id")
                    return node_id if isinstance(node_id, str) else None
            page_info = threads.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return None
            cursor = page_info.get("endCursor")


async def resolve_review_thread(*, thread_node_id: str, token: str) -> bool:
    """Mark a review thread as resolved via the GraphQL ``resolveReviewThread`` mutation."""
    mutation = """
    mutation Resolve($threadId: ID!) {
      resolveReviewThread(input: {threadId: $threadId}) {
        thread { id isResolved }
      }
    }
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                _GITHUB_GRAPHQL,
                headers={"Authorization": f"Bearer {token}"},
                json={"query": mutation, "variables": {"threadId": thread_node_id}},
                timeout=30,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to resolve review thread %s", thread_node_id)
            return False
    data = response.json()
    if data.get("errors"):
        logger.warning("resolveReviewThread errors: %s", data["errors"])
        return False
    thread = data.get("data", {}).get("resolveReviewThread", {}).get("thread", {})
    return bool(thread.get("isResolved"))


async def reply_to_review_comment(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    review_comment_id: int,
    body: str,
    token: str,
) -> dict[str, Any] | None:
    """Reply to an existing pull request review comment thread."""
    url = (
        f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/"
        f"{pr_number}/comments/{review_comment_id}/replies"
    )
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers=_github_headers(token),
                json={"body": body},
                timeout=30,
            )
            if response.status_code == 401:
                raise GitHubAuthError(
                    f"GitHub returned 401 replying to review comment {review_comment_id}"
                )
            response.raise_for_status()
        except GitHubAuthError:
            raise
        except httpx.HTTPError:
            logger.exception(
                "Failed to reply to review comment %s on %s/%s#%s",
                review_comment_id,
                owner,
                repo,
                pr_number,
            )
            return None
    data = response.json()
    return data if isinstance(data, dict) else None


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_HEADERS_VERSION,
    }
