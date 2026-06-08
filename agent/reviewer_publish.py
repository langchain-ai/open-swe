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

import json
import logging
import re
from typing import Any, TypedDict

import httpx

from .reviewer_findings import (
    DiffSide,
    Finding,
    get_thread_metadata,
    normalize_finding_title,
    set_reviewer_thread_metadata,
)
from .utils.dashboard_links import dashboard_thread_url
from .utils.github_token import GitHubAuthError

logger = logging.getLogger(__name__)


_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_GRAPHQL = "https://api.github.com/graphql"
_GITHUB_HEADERS_VERSION = "2022-11-28"
_OPEN_SWE_REVIEW_COMMENT_MARKER_RE = re.compile(
    r"<!--\s*open-swe-review-comment\s+(\{.*?\})\s*-->",
    re.DOTALL,
)


class ReviewCommentMarker(TypedDict):
    id: str
    file_path: str
    start_line: int | None
    end_line: int | None
    side: DiffSide


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def parse_review_comment_marker(body: str) -> ReviewCommentMarker | None:
    match = _OPEN_SWE_REVIEW_COMMENT_MARKER_RE.search(body)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    finding_id = payload.get("id")
    file_path = payload.get("file_path")
    side_raw = payload.get("side", "RIGHT")
    if not isinstance(finding_id, str) or not finding_id:
        return None
    if not isinstance(file_path, str) or not file_path:
        return None
    if side_raw not in {"LEFT", "RIGHT"}:
        return None
    side: DiffSide = "LEFT" if side_raw == "LEFT" else "RIGHT"
    return {
        "id": finding_id,
        "file_path": file_path,
        "start_line": _optional_int(payload.get("start_line")),
        "end_line": _optional_int(payload.get("end_line")),
        "side": side,
    }


def render_inline_comment_body(finding: Finding) -> str:
    """Render the body of one inline review comment.

    Format:

        <!-- metadata marker -->

        🟡 **Generated finding title**

        <finding description detail>

        *(Refers to lines X-Y)*

        ---
        *Your feedback helps Open SWE learn. React with 👍 or 👎 to tell us if this review comment was useful.*

        ```suggestion
        <replacement>
        ```

    The suggestion block is only included when ``finding.suggestion`` is set.
    Multi-line suggestions just become multi-line ```suggestion``` blocks.
    """
    description = (finding.get("description") or "").strip()
    severity = finding.get("severity") or "medium"
    marker_payload = {
        "id": finding.get("id", ""),
        "file_path": finding.get("file", ""),
        "start_line": finding.get("start_line"),
        "end_line": finding.get("end_line"),
        "side": finding.get("side", "RIGHT"),
    }
    marker = f"<!-- open-swe-review-comment {json.dumps(marker_payload, separators=(',', ':'))} -->"

    title, detail = _split_title_and_detail(description, finding.get("title"))
    line_ref = _format_line_reference(finding.get("start_line"), finding.get("end_line"))

    body_parts = [marker, "", f"{_severity_emoji(severity)} **{title}**"]
    if detail:
        body_parts.extend(["", detail])
    if line_ref:
        body_parts.extend(["", line_ref])
    body_parts.extend(
        [
            "",
            "---",
            "*Your feedback helps Open SWE learn. React with 👍 or 👎 to tell us if this review comment was useful.*",
        ]
    )
    body = "\n".join(body_parts)

    suggestion = finding.get("suggestion")
    if suggestion:
        body = f"{body}\n\n```suggestion\n{suggestion}\n```"
    return body


def _severity_emoji(severity: str) -> str:
    return {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🔵",
    }.get(severity, "🟡")


def _split_title_and_detail(description: str, title: object = None) -> tuple[str, str]:
    """Split a generated finding title from the review comment detail."""
    if isinstance(title, str) and title.strip():
        normalized_title = normalize_finding_title(title)
        detail = description.strip()
        if detail:
            lines = description.split("\n")
            if normalize_finding_title(lines[0]) == normalized_title:
                detail = "\n".join(lines[1:]).strip()
        return normalized_title, detail

    if not description:
        return normalize_finding_title(None), ""
    lines = description.split("\n")
    first_line = lines[0].strip()
    detail = "\n".join(lines[1:]).strip()
    normalized_title = normalize_finding_title(first_line)
    if not detail and normalized_title != " ".join(first_line.split()):
        return normalized_title, description
    return normalized_title, detail


def _format_line_reference(start_line: int | None, end_line: int | None) -> str:
    """Format the line reference footer."""
    if end_line is None:
        return ""
    if start_line is None or start_line == end_line:
        return f"*(Refers to line {end_line})*"
    return f"*(Refers to lines {start_line}-{end_line})*"


def render_resolution_comment(
    finding: Finding,
    status: str,
    note: str | None = None,
) -> str | None:
    """Render the agent-provided resolution reply for a review thread."""
    body = _resolution_body(finding, note)
    if body is None:
        return None
    if status == "resolved":
        return f"✅ **Resolved**: {body}"
    return f"❌ **Dismissed**: {body}"


def _resolution_body(finding: Finding, note: str | None) -> str | None:
    candidates = [note, finding.get("resolution_note"), finding.get("last_update_note")]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


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


def review_summary_marker(pr_number: int) -> str:
    """The hidden marker embedded in every Open SWE review summary body.

    Used both to stamp the summary (``render_review_body``) and to detect
    (``open_swe_review_exists``) whether Open SWE has already reviewed a PR.
    """
    return f"<!-- open-swe-reviewer pr={pr_number} -->"


def render_out_of_diff_section(findings: list[Finding]) -> str:
    """Render findings anchored outside the PR diff as a collapsed dropdown.

    These can't be posted as inline comments (GitHub rejects off-diff lines), so
    they live in the review summary body inside a ``<details>`` block — visible
    on demand without adding noise to the changed-line review.
    """
    count = len(findings)
    noun = "finding" if count == 1 else "findings"
    items: list[str] = []
    for f in findings:
        title, detail = _split_title_and_detail(
            (f.get("description") or "").strip(), f.get("title")
        )
        location = f.get("file") or "?"
        line_ref = _format_line_reference(f.get("start_line"), f.get("end_line"))
        if line_ref:
            location += f" {line_ref.strip('*()')}".replace("Refers to ", "")
        item = f"- {_severity_emoji(f.get('severity') or 'medium')} **{title}** — `{location}`"
        if detail:
            item += f"\n  {detail}"
        items.append(item)
    return (
        f"<details>\n<summary>🔍 {count} out-of-diff {noun}</summary>\n\n"
        "These relate to code outside this PR's changed lines.\n\n"
        + "\n".join(items)
        + "\n</details>"
    )


def render_review_body(
    *,
    pr_number: int,
    surfaced_count: int,
    trace_url: str | None = None,
    ui_url: str | None = None,
    out_of_diff_findings: list[Finding] | None = None,
) -> str:
    """Compose the top-level review body."""
    out_of_diff_findings = out_of_diff_findings or []
    if surfaced_count == 0 and not out_of_diff_findings:
        headline = (
            "## ✅ Open SWE Review: No issues found\n\n"
            "Open SWE reviewed this PR and found no potential bugs to report."
        )
    elif surfaced_count == 0:
        headline = "**Open SWE Review** found no issues in the changed lines."
    else:
        issue_word = "issue" if surfaced_count == 1 else "issues"
        headline = f"**Open SWE Review** found {surfaced_count} potential {issue_word}."

    parts = [headline]
    if out_of_diff_findings:
        parts.append(render_out_of_diff_section(out_of_diff_findings))
    links = []
    if ui_url:
        links.append(f"[Open in Web]({ui_url})")
    if trace_url:
        links.append(f"[View Open SWE trace]({trace_url})")
    if links:
        parts.append(" • ".join(links))
    parts.append(review_summary_marker(pr_number))
    return "\n\n".join(parts)


def status_comment_marker(pr_number: int) -> str:
    """Hidden marker stamped on the live status comment for a PR."""
    return f"<!-- open-swe-reviewer-status pr={pr_number} -->"


def render_status_comment(
    *,
    pr_number: int,
    thread_id: str | None = None,
    trace_url: str | None = None,
) -> str:
    """Compose the transient "review in progress" comment.

    Posted when a review starts so the PR shows activity and a clickable
    "Open in Web" link while the run is live; deleted once ``publish_review``
    posts the review (which carries the same link).
    """
    parts = ["## 🔍 Open SWE Review: in progress\n\nOpen SWE is reviewing this PR…"]
    links = []
    ui_url = dashboard_thread_url(thread_id) if thread_id else None
    if ui_url:
        links.append(f"[Open in Web]({ui_url})")
    if trace_url:
        links.append(f"[View Open SWE trace]({trace_url})")
    if links:
        parts.append(" • ".join(links))
    parts.append(status_comment_marker(pr_number))
    return "\n\n".join(parts)


async def post_status_comment(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    token: str,
) -> int | None:
    """POST the live status comment to a PR. Returns its comment id or None."""
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url, headers=_github_headers(token), json={"body": body}, timeout=30
            )
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to post status comment for %s/%s#%s", owner, repo, pr_number)
            return None
    data = response.json()
    comment_id = data.get("id") if isinstance(data, dict) else None
    return comment_id if isinstance(comment_id, int) else None


async def delete_status_comment(
    *,
    owner: str,
    repo: str,
    comment_id: int,
    token: str,
) -> bool:
    """DELETE a status comment by id. Returns True on success (or if gone)."""
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/issues/comments/{comment_id}"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.delete(url, headers=_github_headers(token), timeout=30)
            if response.status_code == 404:  # noqa: PLR2004
                return True
            response.raise_for_status()
        except httpx.HTTPError:
            logger.exception("Failed to delete status comment %s on %s/%s", comment_id, owner, repo)
            return False
    return True


async def post_review_started_comment(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    trace_url: str | None = None,
) -> int | None:
    """Post (or refresh) the transient "review in progress" comment.

    Reuses the ``status_comment_id`` persisted in reviewer thread metadata when
    one already lingers (a prior run that never settled), otherwise posts a
    fresh comment and stores its id so ``clear_review_started_comment`` can
    delete it once the review lands.
    """
    metadata = await get_thread_metadata(thread_id)
    existing_id = metadata.get("status_comment_id")
    if isinstance(existing_id, int):
        await delete_status_comment(owner=owner, repo=repo, comment_id=existing_id, token=token)
    body = render_status_comment(pr_number=pr_number, thread_id=thread_id, trace_url=trace_url)
    new_id = await post_status_comment(
        owner=owner, repo=repo, pr_number=pr_number, body=body, token=token
    )
    await set_reviewer_thread_metadata(thread_id, extra={"status_comment_id": new_id})
    return new_id


async def clear_review_started_comment(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    token: str,
) -> None:
    """Delete the transient "review in progress" comment, if one is tracked."""
    metadata = await get_thread_metadata(thread_id)
    comment_id = metadata.get("status_comment_id")
    if not isinstance(comment_id, int):
        return
    await delete_status_comment(owner=owner, repo=repo, comment_id=comment_id, token=token)
    await set_reviewer_thread_metadata(thread_id, extra={"status_comment_id": None})


async def open_swe_review_exists(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
) -> bool:
    """Return True if Open SWE has already posted a review summary on this PR.

    Detected via the ``review_summary_marker`` that ``render_review_body``
    embeds in every Open SWE review body. The reviewer uses this to avoid
    posting a duplicate "No issues found" summary when the ``re_review`` config
    flag is stale — a push that lands mid-run is delivered as a queued message
    into the still-running first-review run, whose configurable still says
    ``re_review=False``, so the empty-review guard can't trust that flag alone.

    On any API failure this returns False (fail open): the only consequence is
    a possible duplicate summary, never a suppressed first review.
    """
    marker = review_summary_marker(pr_number)
    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = _github_headers(token)
    params: dict[str, Any] = {"per_page": 100, "page": 1}
    async with httpx.AsyncClient() as client:
        while True:
            try:
                response = await client.get(url, headers=headers, params=params, timeout=30)
                response.raise_for_status()
            except httpx.HTTPError:
                logger.exception(
                    "Failed to list PR reviews for %s/%s#%s",
                    owner,
                    repo,
                    pr_number,
                )
                return False
            data = response.json()
            if not isinstance(data, list) or not data:
                return False
            for review in data:
                if isinstance(review, dict) and marker in (review.get("body") or ""):
                    return True
            if len(data) < 100:  # noqa: PLR2004
                return False
            params["page"] += 1


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
            # GitHub returns 422 with errors like "Path could not be resolved"
            # or "Line could not be resolved" when an inline comment's anchor
            # is not part of the PR diff. Surface that as a structured signal
            # so the tool layer can prune the offending findings and retry
            # once, instead of the agent retrying with byte-identical args.
            error_kind: str | None = None
            raw_errors: list[Any] = []
            if e.response.status_code == 422:
                try:
                    parsed = e.response.json()
                    if isinstance(parsed, dict):
                        candidate = parsed.get("errors", [])
                        if isinstance(candidate, list):
                            raw_errors = candidate
                except Exception:  # noqa: BLE001 — body may not be JSON
                    raw_errors = []
                if any(
                    isinstance(err, str)
                    and ("Path could not be resolved" in err or "Line could not be resolved" in err)
                    for err in raw_errors
                ):
                    error_kind = "unresolved_anchor"
            return {
                "_error": f"HTTP {e.response.status_code}: {body}",
                "_error_kind": error_kind,
                "_raw_errors": raw_errors,
                "_status": e.response.status_code,
            }
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
            data_root = data.get("data") if isinstance(data, dict) else None
            repository = data_root.get("repository") if isinstance(data_root, dict) else None
            if not isinstance(repository, dict):
                logger.warning(
                    "Null repository in review-threads response for %s/%s#%s "
                    "(token likely lacks access: SAML, expired token, or private/deleted repo)",
                    owner,
                    repo,
                    pr_number,
                )
                return out
            pull_request = repository.get("pullRequest")
            threads = pull_request.get("reviewThreads") if isinstance(pull_request, dict) else None
            if not isinstance(threads, dict):
                return out
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
            if not isinstance(data, dict):
                return None
            data_root = data.get("data")
            if not isinstance(data_root, dict):
                return None
            repository = data_root.get("repository")
            if not isinstance(repository, dict):
                return None
            pull_request = repository.get("pullRequest")
            if not isinstance(pull_request, dict):
                return None
            threads = pull_request.get("reviewThreads")
            if not isinstance(threads, dict):
                return None
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
    data_root = data.get("data") if isinstance(data, dict) else None
    resolved = data_root.get("resolveReviewThread") if isinstance(data_root, dict) else None
    thread = resolved.get("thread") if isinstance(resolved, dict) else None
    return bool(thread.get("isResolved")) if isinstance(thread, dict) else False


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
