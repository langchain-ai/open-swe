"""GitHub Reviews API + GraphQL resolveReviewThread for the reviewer agent.

The reviewer agent calls ``publish_review`` at the end of a run. That tool
batches eligible findings (severity ≥ threshold, status=open, capped) into a
single GitHub PR Review:

- Review body: agent-authored summary line.
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

import logging
from typing import Any

import httpx

from .reviewer_findings import Finding

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
    suggestion = finding.get("suggestion")
    if not suggestion:
        return description
    return f"{description}\n\n```suggestion\n{suggestion}\n```"


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


def render_review_body(
    *,
    pr_number: int,
    surfaced_count: int,
    total_open_count: int,
    severity_threshold: str,
    summary: str | None,
) -> str:
    """Compose the top-level review body.

    Includes the agent's summary (if any) and a footer line so reviewers know
    when findings were filtered out below the surfacing threshold.
    """
    parts: list[str] = []
    if summary:
        parts.append(summary.strip())
    if surfaced_count == 0:
        parts.append("_No issues at or above the configured severity threshold._")
    else:
        hidden = total_open_count - surfaced_count
        if hidden > 0:
            parts.append(
                f"_Showing {surfaced_count} finding{'s' if surfaced_count != 1 else ''} "
                f"at severity ≥ `{severity_threshold}`; {hidden} lower-severity "
                f"finding{'s' if hidden != 1 else ''} hidden._"
            )
    parts.append(f"<!-- open-swe-reviewer pr={pr_number} -->")
    return "\n\n".join(p for p in parts if p)


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
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to POST PR review for %s/%s#%s", owner, repo, pr_number)
            return None
    data = response.json()
    return data if isinstance(data, dict) else None


async def fetch_review_comments(
    *,
    owner: str,
    repo: str,
    review_id: int,
    token: str,
) -> list[dict[str, Any]]:
    """List the inline comments for a posted review.

    GitHub's review-creation response includes a ``comments`` count but not the
    per-comment IDs in all paths; this paginates the canonical list endpoint.
    """
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/reviews/{review_id}/comments"
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


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GITHUB_HEADERS_VERSION,
    }
