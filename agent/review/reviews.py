"""Read API for the PR review UI.

Reviewer threads (``metadata.kind == "reviewer"``) hold the durable review
state for a PR: identity (``pr``), watch flag, and head SHA; their findings
live in PostgreSQL. These endpoints surface that state plus live PR
details/diff fetched from GitHub with the App installation token.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx2
from fastapi import HTTPException, Response
from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, ValidationError

from agent.database import postgres
from agent.github.app import get_github_app_installation_token
from agent.github.checks import github_headers
from agent.github.ci import list_check_runs, list_commit_statuses
from agent.github.http import github_client
from agent.github.pull_request_diff import build_pr_diff_files
from agent.github.pull_request_status import fetch_unresolved_review_threads
from agent.github.webhook import trigger_pr_review_from_ref
from agent.review.assessment_feedback import ASSESSMENTS
from agent.review.author_guidance import GuidanceView
from agent.review.findings import (
    REVIEWER_THREAD_KIND,
    Finding,
    FindingLike,
    comment_ids_for_finding,
    findings_by_thread,
    is_thread_resolved,
)
from agent.review_scout.launch import ReviewScoutTarget
from agent.thread_ids import reviewer_thread_id
from agent.utils.json_types import ThreadLike, as_json_object, thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_TIMEOUT = httpx2.Timeout(15.0, connect=5.0)


async def _require_app_token() -> str:
    token = await get_github_app_installation_token()
    if not token:
        raise HTTPException(503, "GitHub App token unavailable")
    return token


async def _github_get(
    path: str, token: str, *, accept: str | None = None, params: dict[str, Any] | None = None
) -> Any:
    headers = github_headers(token)
    if accept:
        headers["Accept"] = accept
    async with httpx2.AsyncClient(timeout=_GITHUB_TIMEOUT) as client:
        response = await client.get(f"{_GITHUB_API}{path}", headers=headers, params=params)
    if response.status_code == 404:
        raise HTTPException(404, "not found on GitHub")
    if response.status_code >= 400:
        logger.warning("GitHub GET %s failed: %s", path, response.status_code)
        raise HTTPException(502, f"GitHub request failed ({response.status_code})")
    if accept and "json" not in accept:
        return response.text
    return response.json()


def _github_error_message(response: httpx2.Response) -> str:
    """Best-effort extraction of GitHub's error message for surfacing to the UI."""
    fallback = f"GitHub request failed ({response.status_code})"
    try:
        data = response.json()
    except ValueError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    message = data.get("message")
    message_str = message if isinstance(message, str) else ""
    errors = data.get("errors")
    detail_parts: list[str] = []
    if isinstance(errors, list):
        for err in errors:
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                detail_parts.append(err["message"])
    detail = "; ".join(detail_parts)
    if message_str and detail:
        return f"{message_str}: {detail}"
    return message_str or detail or fallback


async def _github_write(
    method: Literal["POST", "PATCH"], path: str, token: str, *, json: dict[str, Any]
) -> Any:
    async with httpx2.AsyncClient(timeout=_GITHUB_TIMEOUT) as client:
        response = await client.request(
            method, f"{_GITHUB_API}{path}", headers=github_headers(token), json=json
        )
    if response.status_code >= 400:
        message = _github_error_message(response)
        logger.warning("GitHub %s %s failed: %s %s", method, path, response.status_code, message)
        # Pass 4xx through verbatim (422 = line not in diff, 403 = perms); collapse
        # 5xx to a 502 so a GitHub outage doesn't masquerade as a client error.
        raise HTTPException(response.status_code if response.status_code < 500 else 502, message)
    return response.json()


async def _github_post(path: str, token: str, *, json: dict[str, Any]) -> Any:
    return await _github_write("POST", path, token, json=json)


async def _thread_findings(threads: list[ThreadLike]) -> dict[str, list[Finding]]:
    return await findings_by_thread(
        {
            thread_id: thread_metadata(thread)
            for thread in threads
            if isinstance(thread_id := thread.get("thread_id"), str)
        }
    )


def _serialize_finding(finding: FindingLike, head_sha: str | None) -> dict[str, Any]:
    last_confirmed = finding.get("last_confirmed_sha")
    outdated = bool(
        head_sha
        and isinstance(last_confirmed, str)
        and last_confirmed
        and last_confirmed != head_sha
    )
    interactions = finding.get("interactions")
    comment_ids = comment_ids_for_finding(finding)
    return {
        "id": finding.get("id"),
        "severity": finding.get("severity", "low"),
        "confidence": finding.get("confidence", "medium"),
        "category": finding.get("category", ""),
        "title": finding.get("title") or "",
        "description": finding.get("description", ""),
        "suggestion": finding.get("suggestion"),
        "file": finding.get("file", ""),
        "start_line": finding.get("start_line"),
        "end_line": finding.get("end_line"),
        "side": finding.get("side", "RIGHT"),
        "in_diff": bool(finding.get("in_diff", True)),
        "status": finding.get("status", "open"),
        "outdated": outdated,
        "resolution_note": finding.get("resolution_note"),
        "diff_hunk": finding.get("diff_hunk"),
        "github_thread_resolved": is_thread_resolved(finding),
        "github_review_comment_id": comment_ids[0] if comment_ids else None,
        "interactions": interactions if isinstance(interactions, list) else [],
    }


_BUG_SEVERITIES = frozenset({"high", "critical"})


def classify_finding(finding: FindingLike) -> Literal["bug", "investigate", "informational"]:
    """Map our severity/confidence model onto the UI's Bugs/Flags split."""
    severity = finding.get("severity", "low")
    confidence = finding.get("confidence", "medium")
    if severity in _BUG_SEVERITIES and confidence == "high":
        return "bug"
    if severity != "low":
        return "investigate"
    return "informational"


def _finding_counts(findings: Sequence[FindingLike]) -> dict[str, int]:
    counts = {"open": 0, "resolved": 0, "dismissed": 0, "bugs": 0, "flags": 0}
    for finding in findings:
        status = finding.get("status", "open")
        if status in counts:
            counts[status] += 1
        if status == "open":
            if classify_finding(finding) == "bug":
                counts["bugs"] += 1
            else:
                counts["flags"] += 1
    return counts


class ReviewCounts(BaseModel):
    open: int
    resolved: int
    dismissed: int
    bugs: int
    flags: int


class ReviewSummary(BaseModel):
    """A reviewer thread's durable state, as the dashboard reads it."""

    thread_id: str
    owner: str
    repo: str
    full_name: str
    number: int
    title: str
    url: str
    head_ref: str
    base_ref: str
    author: str
    head_sha: str
    watch: bool
    status: Literal["running", "error", "idle"]
    counts: ReviewCounts
    updated_at: str | None = None


def _run_status(thread: ThreadLike, metadata: dict[str, Any]) -> str:
    if thread.get("status") == "busy":
        return "running"
    latest = metadata.get("latest_run_status")
    if latest in {"pending", "running"}:
        return "running"
    if latest in {"error", "failed", "timeout", "interrupted"}:
        return "error"
    return "idle"


def _thread_review_summary(
    thread: ThreadLike, findings: Sequence[FindingLike]
) -> dict[str, Any] | None:
    metadata = thread_metadata(thread)
    pr = metadata.get("pr")
    if not isinstance(pr, dict):
        return None
    owner = pr.get("owner")
    name = pr.get("name")
    number = pr.get("number")
    if not (isinstance(owner, str) and isinstance(name, str) and isinstance(number, int)):
        return None
    updated_at = thread.get("updated_at")
    return {
        "thread_id": thread.get("thread_id"),
        "owner": owner,
        "repo": name,
        "full_name": f"{owner}/{name}",
        "number": number,
        "title": pr.get("title") or f"PR #{number}",
        "url": pr.get("url") or f"https://github.com/{owner}/{name}/pull/{number}",
        "head_ref": pr.get("head_ref") or "",
        "base_ref": pr.get("base_ref") or "",
        "author": pr.get("author") if isinstance(pr.get("author"), str) else "",
        "head_sha": metadata.get("head_sha") or "",
        "watch": bool(metadata.get("watch")),
        "status": _run_status(thread, metadata),
        "counts": _finding_counts(findings),
        "updated_at": updated_at if isinstance(updated_at, str) else None,
    }


async def _review_summary_of(thread: ThreadLike) -> dict[str, Any] | None:
    findings = await _thread_findings([thread])
    return _thread_review_summary(thread, findings.get(thread.get("thread_id"), []))


async def list_reviews(
    limit: int = 20,
    *,
    offset: int = 0,
    author: str | None = None,
    is_accessible: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
    page_size: int = 100,
    max_scan: int = 1000,
) -> tuple[list[dict[str, Any]], bool]:
    """List review summaries, newest first.

    Returns ``(summaries, has_more)`` where the summaries are the page at
    ``offset`` (counted in accessible, filter-matching records) and
    ``has_more`` says whether at least one more record exists past it.

    ``author`` is pushed into the ``threads.search`` metadata filter
    (``pr.author`` containment), so the "My PRs" tab only fetches the user's
    own reviewer threads instead of scanning the whole population in Python.

    When ``is_accessible`` is given, keeps paging through reviewer threads
    until enough accessible summaries are collected (or ``max_scan`` threads
    have been examined), so inaccessible records don't crowd accessible ones
    out of a single fixed-size page.
    """
    client = langgraph_client()
    search_metadata: dict[str, Any] = {"kind": REVIEWER_THREAD_KIND}
    if author is not None:
        search_metadata["pr"] = {"author": author}
    needed = offset + limit + 1
    summaries: list[dict[str, Any]] = []
    scan_offset = 0
    while len(summaries) < needed and scan_offset < max_scan:
        threads = await client.threads.search(
            metadata=search_metadata,
            limit=page_size,
            offset=scan_offset,
            sort_by="updated_at",
            sort_order="desc",
        )
        if not threads:
            break
        findings = await _thread_findings(threads)
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            summary = _thread_review_summary(thread, findings.get(thread.get("thread_id"), []))
            if not summary:
                continue
            if is_accessible is not None and not await is_accessible(summary):
                continue
            summaries.append(summary)
            if len(summaries) >= needed:
                break
        if len(threads) < page_size:
            break
        scan_offset += page_size
    page = summaries[offset : offset + limit]
    return page, len(summaries) > offset + limit


async def get_review_summaries(
    identities: list[tuple[str, str, int]],
) -> dict[str, ReviewSummary | None]:
    """Read review indicators for already-authorized pull requests."""
    client = langgraph_client()
    semaphore = asyncio.Semaphore(4)

    async def read(owner: str, repo: str, number: int) -> tuple[str, ReviewSummary | None]:
        async with semaphore:
            threads = await client.threads.search(
                metadata={
                    "kind": REVIEWER_THREAD_KIND,
                    "pr": {"owner": owner, "name": repo, "number": number},
                },
                limit=1,
                sort_by="updated_at",
                sort_order="desc",
            )
            raw = await _review_summary_of(threads[0]) if threads else None
            return f"{owner}/{repo}#{number}".lower(), _as_review_summary(raw)

    return dict(await asyncio.gather(*(read(*identity) for identity in identities)))


def _as_review_summary(raw: dict[str, Any] | None) -> ReviewSummary | None:
    if raw is None:
        return None
    try:
        return ReviewSummary.model_validate(raw)
    except ValidationError:
        logger.warning(
            "Reviewer thread metadata does not describe a review summary",
            extra={"review_thread_id": raw.get("thread_id")},
            exc_info=True,
        )
        return None


def _user_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    login = value.get("login")
    if not isinstance(login, str):
        return None
    return {"login": login, "avatar_url": value.get("avatar_url")}


def _serialize_pr_details(payload: dict[str, Any]) -> dict[str, Any]:
    labels = payload.get("labels")
    state = payload.get("state")
    if payload.get("merged"):
        state = "merged"
    elif payload.get("draft"):
        state = "draft"
    return {
        "state": state if isinstance(state, str) else "open",
        "title": payload.get("title") or "",
        "body": payload.get("body") or "",
        "additions": payload.get("additions") or 0,
        "deletions": payload.get("deletions") or 0,
        "changed_files": payload.get("changed_files") or 0,
        "commits": payload.get("commits") or 0,
        "head_sha": (payload.get("head") or {}).get("sha") or "",
        "head_ref": (payload.get("head") or {}).get("ref") or "",
        "base_ref": (payload.get("base") or {}).get("ref") or "",
        "author": _user_ref(payload.get("user")),
        "assignees": [
            user
            for user in (_user_ref(value) for value in payload.get("assignees") or [])
            if user is not None
        ],
        "requested_reviewers": [
            user
            for user in (_user_ref(value) for value in payload.get("requested_reviewers") or [])
            if user is not None
        ],
        "labels": [
            {"name": label.get("name"), "color": label.get("color")}
            for label in (labels if isinstance(labels, list) else [])
            if isinstance(label, dict) and isinstance(label.get("name"), str)
        ],
    }


async def _fetch_check_runs(owner: str, repo: str, sha: str, token: str) -> list[dict[str, Any]]:
    if not sha:
        return []
    try:
        payload = await _github_get(
            f"/repos/{owner}/{repo}/commits/{sha}/check-runs",
            token,
            params={"per_page": 50},
        )
    except HTTPException:
        return []
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    out: list[dict[str, Any]] = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        out.append(
            {
                "name": run.get("name") or "",
                "status": run.get("status") or "",
                "conclusion": run.get("conclusion"),
                "url": run.get("html_url"),
            }
        )
    return out


async def get_pr_head_sha(owner: str, repo: str, pr_number: int) -> str:
    """Return the PR's current head SHA from GitHub, or "" if unavailable.

    A lightweight alternative to :func:`get_review` for callers that only need to
    detect whether the PR head has moved (e.g. the chat staleness check).
    """
    try:
        token = await _require_app_token()
        payload = await _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token)
    except HTTPException:
        return ""
    head = payload.get("head") if isinstance(payload, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    return sha if isinstance(sha, str) else ""


async def create_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    token: str,
    path: str,
    line: int,
    side: Literal["LEFT", "RIGHT"],
    body: str,
    start_line: int | None = None,
    start_side: Literal["LEFT", "RIGHT"] | None = None,
) -> dict[str, Any]:
    """Post a single inline review comment to a PR using the caller's token.

    Unlike the reviewer agent (which batches comments into one review via the App
    token), this posts a standalone comment immediately, authored by the signed-in
    user. ``commit_id`` is the PR's live head SHA. GitHub errors surface verbatim so
    the UI can explain a 422 (line not part of the diff) or 403 (missing permission).
    """
    head_sha = await get_pr_head_sha(owner, repo, pr_number)
    if not head_sha:
        raise HTTPException(502, "could not resolve PR head commit")
    payload: dict[str, Any] = {
        "body": body,
        "commit_id": head_sha,
        "path": path,
        "line": line,
        "side": side,
    }
    # GitHub forbids multi-line ranges that span sides; only add the range start
    # when it is a distinct earlier line on the same side.
    if start_line is not None and start_line != line:
        payload["start_line"] = start_line
        payload["start_side"] = start_side or side
    return await _github_post(
        f"/repos/{owner}/{repo}/pulls/{pr_number}/comments", token, json=payload
    )


async def update_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment_id: int,
    *,
    token: str,
    viewer_login: str,
    body: str,
) -> dict[str, Any]:
    """Update an inline review comment owned by the signed-in user."""
    path = f"/repos/{owner}/{repo}/pulls/comments/{comment_id}"
    comment = as_json_object(await _github_get(path, token))
    author = as_json_object(comment.get("user")).get("login")
    pull_request_url = comment.get("pull_request_url")
    expected_pr_url = f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"
    if (
        not isinstance(author, str)
        or author.lower() != viewer_login.lower()
        or not isinstance(pull_request_url, str)
        or pull_request_url.lower() != expected_pr_url.lower()
    ):
        raise HTTPException(403, "comment is not editable by this user")
    return await _github_write("PATCH", path, token, json={"body": body})


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Inline comments the reviewer posts carry this hidden marker (see reviewer_publish).
_OPEN_SWE_COMMENT_RE = re.compile(r"<!--\s*open-swe-review-comment\b")
_REVIEW_COMMENTS_PER_PAGE = 100
# Bound the fetch so a pathological PR can't trigger unbounded paging (~2000 comments).
_MAX_REVIEW_COMMENT_PAGES = 20


def _clean_comment_body(body: str) -> str:
    return _HTML_COMMENT_RE.sub("", body).strip()


def _normalize_review_comment(item: dict[str, Any]) -> dict[str, Any]:
    raw_body = item.get("body")
    body = raw_body if isinstance(raw_body, str) else ""
    user = as_json_object(item.get("user"))
    line = item.get("line")
    if not isinstance(line, int):
        original = item.get("original_line")
        line = original if isinstance(original, int) else None
    return {
        "id": item.get("id"),
        "author": user.get("login") if isinstance(user.get("login"), str) else "",
        "author_avatar_url": (
            user.get("avatar_url") if isinstance(user.get("avatar_url"), str) else ""
        ),
        "path": item.get("path") if isinstance(item.get("path"), str) else "",
        "line": line,
        "side": item.get("side") if item.get("side") in ("LEFT", "RIGHT") else "RIGHT",
        "body": _clean_comment_body(body),
        "html_url": item.get("html_url") if isinstance(item.get("html_url"), str) else "",
        "created_at": item.get("created_at") if isinstance(item.get("created_at"), str) else "",
        "is_open_swe": bool(_OPEN_SWE_COMMENT_RE.search(body)),
        # GitHub nulls `position` when the line no longer appears in the current
        # diff — i.e. the comment is outdated and can't be rendered inline.
        "is_outdated": not isinstance(item.get("position"), int),
    }


async def list_review_comments(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    """List inline review comments on a PR (newest first), normalized for the UI.

    Surfaces every inline comment on the PR — including humans' — not just the
    reviewer's findings. ``is_open_swe`` flags the reviewer's own (marker-bearing)
    comments so the UI can separate them from other people's. Pages through the
    full list (bounded by ``_MAX_REVIEW_COMMENT_PAGES``) so older comments aren't
    silently dropped.
    """
    token = await _require_app_token()
    comments: list[dict[str, Any]] = []
    for page in range(1, _MAX_REVIEW_COMMENT_PAGES + 1):
        raw = await _github_get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            token,
            params={
                "per_page": _REVIEW_COMMENTS_PER_PAGE,
                "page": page,
                "sort": "created",
                "direction": "desc",
            },
        )
        if not isinstance(raw, list) or not raw:
            break
        comments.extend(_normalize_review_comment(item) for item in raw if isinstance(item, dict))
        if len(raw) < _REVIEW_COMMENTS_PER_PAGE:
            break
    return {"comments": comments}


async def _reviewer_thread_for(owner: str, repo: str, pr_number: int) -> ThreadLike | None:
    """The reviewer thread for this PR, or ``None`` when no review has run."""
    try:
        thread = await langgraph_client().threads.get(reviewer_thread_id(owner, repo, pr_number))
    except NotFoundError:
        return None
    except Exception:  # noqa: BLE001
        logger.warning(
            "reviewer thread read failed",
            exc_info=True,
            extra={"repo_full_name": f"{owner}/{repo}", "pr_number": pr_number},
        )
        return None
    return thread if isinstance(thread, dict) else None


def _unreviewed_summary(
    owner: str, repo: str, pr_number: int, pr_payload: dict[str, Any]
) -> dict[str, Any]:
    """A review summary for a PR the reviewer has never run on."""
    author = pr_payload.get("user")
    return {
        "thread_id": None,
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "number": pr_number,
        "title": pr_payload.get("title") or f"PR #{pr_number}",
        "url": pr_payload.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pr_number}",
        "head_ref": (pr_payload.get("head") or {}).get("ref") or "",
        "base_ref": (pr_payload.get("base") or {}).get("ref") or "",
        "author": (author.get("login") or "") if isinstance(author, dict) else "",
        "head_sha": (pr_payload.get("head") or {}).get("sha") or "",
        "watch": False,
        "status": "none",
        "counts": _finding_counts([]),
        "updated_at": pr_payload.get("updated_at"),
    }


async def get_review(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    """The review page payload: the PR itself, plus review results when they exist.

    The reviewer graph is optional — a PR it has never run on still renders with
    its GitHub-sourced details, checks and diff, and no findings.
    """
    token = await _require_app_token()
    raw_pr = await _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token)
    pr_payload = raw_pr if isinstance(raw_pr, dict) else {}
    details = _serialize_pr_details(pr_payload)

    thread = await _reviewer_thread_for(owner, repo, pr_number)
    stored = (await _thread_findings([thread])).get(thread.get("thread_id"), []) if thread else []
    summary = _thread_review_summary(thread, stored) if thread else None
    metadata = thread_metadata(thread) if thread else {}
    if not summary:
        summary = _unreviewed_summary(owner, repo, pr_number, pr_payload)

    head_sha = details["head_sha"] or summary["head_sha"]
    checks = await _fetch_check_runs(owner, repo, head_sha, token)

    findings = [_serialize_finding(finding, head_sha) for finding in stored]
    findings.sort(
        key=lambda f: (
            f["status"] != "open",
            {"bug": 0, "investigate": 1, "informational": 2}[classify_finding(f)],
            f["file"],
            f["start_line"] or 0,
        )
    )
    for finding in findings:
        finding["group"] = classify_finding(finding)

    target = await _scout_target(owner, repo, pr_number, pr_payload)
    walkthrough = await target.walkthrough() if target else None
    walkthrough_running = walkthrough is None and target is not None and await _scouting(target)
    assessment_id = metadata.get("review_assessment_id")
    assessment = (
        await ASSESSMENTS.get(str(assessment_id)) if isinstance(assessment_id, int) else None
    )

    return {
        **summary,
        "pr": details,
        "checks": checks,
        "findings": findings,
        "walkthrough": walkthrough.model_dump(mode="json") if walkthrough else None,
        "walkthrough_running": walkthrough_running,
        "assessment": assessment.model_dump() if assessment else None,
        "guidance": [
            point.model_dump(mode="json")
            for point in await GuidanceView.for_pull_request(owner, repo, pr_number)
        ],
    }


_PREVIEW_FILE_LIMIT = 10
_PREVIEW_FILES_PER_PAGE = 100


class PreviewFile(BaseModel):
    path: str
    status: str
    additions: int
    deletions: int


class PreviewThread(BaseModel):
    author: str | None = None
    body: str
    path: str
    line: int | None = None
    url: str | None = None


class PreviewCheck(BaseModel):
    name: str
    status: str
    conclusion: str | None = None
    url: str | None = None


class PullRequestPreview(BaseModel):
    title: str
    body: str
    author: str | None
    author_avatar_url: str | None
    state: str
    draft: bool
    head_ref: str
    base_ref: str
    commits: int
    additions: int
    deletions: int
    changed_files: int
    files: list[PreviewFile]
    # None when GitHub could not answer, which is not the same as none unresolved
    # or no checks configured.
    unresolved: list[PreviewThread] | None
    checks: list[PreviewCheck] | None
    guidance: list[GuidanceView]


class _GithubPreviewFile(BaseModel):
    filename: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0


class _GithubCheckRun(BaseModel):
    name: str = ""
    status: str = "completed"
    conclusion: str | None = None
    html_url: str | None = None


class _GithubCommitStatus(BaseModel):
    context: str = ""
    state: str = "pending"
    target_url: str | None = None

    def as_check(self) -> PreviewCheck:
        """A legacy commit status in check-run terms, which is how the UI reads both."""
        pending = self.state == "pending"
        return PreviewCheck(
            name=self.context,
            status="in_progress" if pending else "completed",
            conclusion=None if pending else self.state,
            url=self.target_url,
        )


class _GithubUser(BaseModel):
    login: str
    avatar_url: str | None = None


class _GithubRef(BaseModel):
    ref: str = ""
    sha: str = ""


class _GithubPreviewPull(BaseModel):
    title: str = ""
    body: str | None = None
    state: str = "open"
    draft: bool = False
    merged: bool = False
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    user: _GithubUser | None = None
    head: _GithubRef | None = None
    base: _GithubRef | None = None


def _preview_thread(thread: dict[str, Any]) -> PreviewThread:
    parsed = PreviewThread.model_validate(thread)
    # Review bots hide their bookkeeping in HTML comments, which would otherwise
    # be the whole of what a one-line preview shows.
    return parsed.model_copy(update={"body": _clean_comment_body(parsed.body)})


async def get_pull_request_preview(
    owner: str, repo: str, pr_number: int, token: str
) -> PullRequestPreview:
    """Description, the largest changed files, and unresolved threads for any PR.

    Unlike ``get_review`` this does not need a reviewer thread, so it answers for
    every PR the viewer can reach. Only file metadata is read — the contents live
    behind ``get_review_diff``, which is far too heavy to open a preview with.

    Reads with the caller's own token rather than the App's: the preview only ever
    shows a PR the caller can already open, so the App installation is beside the
    point here, unlike the published review a reviewer thread backs.
    """
    async with github_client(token=token, timeout=_GITHUB_TIMEOUT) as client:
        pull_payload, file_payload, threads = await asyncio.gather(
            _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token),
            _github_get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/files",
                token,
                params={"per_page": _PREVIEW_FILES_PER_PAGE},
            ),
            fetch_unresolved_review_threads(client, owner, repo, pr_number),
        )
    pull = _GithubPreviewPull.model_validate(pull_payload if isinstance(pull_payload, dict) else {})
    raw_files = file_payload if isinstance(file_payload, list) else []
    files = [
        PreviewFile(
            path=entry.filename,
            status=entry.status,
            additions=entry.additions,
            deletions=entry.deletions,
        )
        for entry in (
            _GithubPreviewFile.model_validate(item) for item in raw_files if isinstance(item, dict)
        )
    ]
    files.sort(key=lambda entry: entry.additions + entry.deletions, reverse=True)
    head_sha = pull.head.sha if pull.head else ""
    # CI reaches GitHub as check runs or as legacy commit statuses, and the PR
    # list counts both — a preview reading only one would contradict the rail.
    raw_checks, raw_statuses = (
        await asyncio.gather(
            list_check_runs(owner=owner, repo=repo, ref=head_sha, token=token),
            list_commit_statuses(owner=owner, repo=repo, ref=head_sha, token=token),
        )
        if head_sha
        else (None, None)
    )
    checks = (
        None
        if raw_checks is None and raw_statuses is None
        else [
            PreviewCheck(
                name=run.name, status=run.status, conclusion=run.conclusion, url=run.html_url
            )
            for run in (
                _GithubCheckRun.model_validate(item)
                for item in (raw_checks or [])
                if isinstance(item, dict)
            )
        ]
        + [
            _GithubCommitStatus.model_validate(item).as_check()
            for item in (raw_statuses or [])
            if isinstance(item, dict)
        ]
    )
    return PullRequestPreview(
        title=pull.title,
        body=pull.body or "",
        author=pull.user.login if pull.user else None,
        author_avatar_url=pull.user.avatar_url if pull.user else None,
        state="merged" if pull.merged else pull.state,
        draft=pull.draft,
        head_ref=pull.head.ref if pull.head else "",
        base_ref=pull.base.ref if pull.base else "",
        commits=pull.commits,
        checks=checks,
        additions=pull.additions,
        deletions=pull.deletions,
        changed_files=pull.changed_files or len(files),
        files=files[:_PREVIEW_FILE_LIMIT],
        unresolved=(None if threads is None else [_preview_thread(thread) for thread in threads]),
        guidance=await GuidanceView.for_pull_request(owner, repo, pr_number),
    )


async def get_review_diff(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    """Return the PR's changed files with full original/modified contents.

    Uses the App installation token so the diff is available regardless of who
    is viewing the review. The client renders these with pierre's MultiFileDiff.
    """
    token = await _require_app_token()
    async with httpx2.AsyncClient(headers=github_headers(token), timeout=_GITHUB_TIMEOUT) as client:
        diff = await build_pr_diff_files(client, f"{owner}/{repo}", pr_number)
    files = diff["files"]
    return {
        "files": files,
        "total_additions": sum(f["additions"] for f in files),
        "total_deletions": sum(f["deletions"] for f in files),
        "truncated": diff["truncated"],
    }


# --- PR description image proxy ----------------------------------------------
# PR bodies can embed images hosted on GitHub (user-attachment uploads,
# *.githubusercontent.com). For private repos those URLs require GitHub auth the
# browser doesn't have, so they render broken. We proxy them through the App
# installation token. The host allowlist + per-redirect public-IP check guard
# against SSRF (only GitHub-owned hosts are ever contacted).

_ALLOWED_IMAGE_HOST_SUFFIXES = (".githubusercontent.com",)
_MAX_IMAGE_REDIRECTS = 5
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
# Only safe raster formats — SVG (image/svg+xml) can execute script in our
# origin, so it is never served.
_ALLOWED_IMAGE_CONTENT_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}
)


def _is_allowed_image_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host == "github.com" or host == "www.github.com":
        # On github.com only user-attachment assets are images worth proxying.
        return parsed.path.startswith("/user-attachments/")
    return any(host.endswith(suffix) for suffix in _ALLOWED_IMAGE_HOST_SUFFIXES)


def _host_resolves_public(hostname: str) -> bool:
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not addr_infos:
        return False
    for addr_info in addr_infos:
        try:
            ip = ipaddress.ip_address(addr_info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def _validate_image_url(url: str) -> None:
    if not _is_allowed_image_url(url):
        raise HTTPException(400, "image host not allowed")
    hostname = urlparse(url).hostname or ""
    if not _host_resolves_public(hostname):
        raise HTTPException(400, "image host not allowed")


async def _require_image_in_pr(owner: str, repo: str, pr_number: int, url: str, token: str) -> None:
    """Bind the requested image to the authorized PR.

    The proxy fetches with the App installation token, which can read every repo
    the App is installed on. Without this check a caller authorized for one repo
    could proxy an image URL from another private repo (IDOR). Only URLs that
    actually appear in this PR's body are allowed.
    """
    pr_payload = await _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token)
    body = pr_payload.get("body") or ""
    if url not in body:
        raise HTTPException(403, "image not referenced by this PR")


async def proxy_pr_image(owner: str, repo: str, pr_number: int, url: str) -> Response:
    """Stream a GitHub-hosted PR image through the App token.

    The URL must appear in the target PR's body (bound to the authorized
    resource), and every URL (including redirect targets) is validated against
    the GitHub host allowlist and a public-IP check before it is contacted.
    """
    _validate_image_url(url)
    token = await _require_app_token()
    await _require_image_in_pr(owner, repo, pr_number, url, token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "image/*"}

    current_url = url
    async with httpx2.AsyncClient(timeout=_GITHUB_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_IMAGE_REDIRECTS + 1):
            async with client.stream("GET", current_url, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise HTTPException(502, "image fetch failed (redirect without target)")
                    current_url = urljoin(str(response.url), location)
                    _validate_image_url(current_url)
                    continue

                if response.status_code >= 400:
                    raise HTTPException(502, f"image fetch failed ({response.status_code})")

                content_type = (
                    response.headers.get("Content-Type", "").lower().split(";", 1)[0].strip()
                )
                if content_type not in _ALLOWED_IMAGE_CONTENT_TYPES:
                    raise HTTPException(415, "unsupported image type")

                # Stream and abort once over the cap so a large (or lying) upstream
                # can't make the worker buffer the whole file.
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_IMAGE_BYTES:
                        raise HTTPException(413, "image too large")

                return Response(
                    content=bytes(content),
                    media_type=content_type,
                    headers={
                        "Cache-Control": "private, max-age=300",
                        "X-Content-Type-Options": "nosniff",
                        "Content-Security-Policy": "default-src 'none'; sandbox",
                    },
                )

    raise HTTPException(502, "too many redirects fetching image")


class _ScoutRef(BaseModel):
    sha: str = ""


class _ScoutPull(BaseModel):
    title: str = ""
    base: _ScoutRef = _ScoutRef()
    head: _ScoutRef = _ScoutRef()


async def _scout_target(
    owner: str, repo: str, pr_number: int, pr_payload: object
) -> ReviewScoutTarget | None:
    """The scout target for the PR's current head, or ``None`` without a database or head."""
    if not postgres.configured():
        return None
    pull = _ScoutPull.model_validate(pr_payload if isinstance(pr_payload, dict) else {})
    if not pull.base.sha or not pull.head.sha:
        return None
    return ReviewScoutTarget(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        pr_title=pull.title,
        base_sha=pull.base.sha,
        head_sha=pull.head.sha,
        workspace_slug=await WORKSPACES.owner_of_repo(f"{owner}/{repo}"),
    )


async def _scouting(target: ReviewScoutTarget) -> bool:
    try:
        return await target.active_run() is not None
    except Exception:
        logger.warning("Could not read review scout runs", exc_info=True, extra=target.log_extra)
        return False


class ReviewScoutTrigger(BaseModel):
    started: bool
    run_id: str | None = None


async def trigger_review_scout(owner: str, repo: str, pr_number: int) -> ReviewScoutTrigger:
    """Start the review scout for the PR's current head, or join the one already running."""
    token = await _require_app_token()
    pr_payload = await _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token)
    target = await _scout_target(owner, repo, pr_number, pr_payload)
    if target is None:
        raise HTTPException(503, "the review scout needs a database and a pull request head")
    if await target.walkthrough() is not None:
        return ReviewScoutTrigger(started=False)
    return ReviewScoutTrigger(started=True, run_id=await target.start())


async def trigger_re_review(owner: str, repo: str, pr_number: int, login: str) -> dict[str, Any]:
    from agent.slack.client import GitHubPrRef

    pr_ref = GitHubPrRef(
        owner=owner,
        repo=repo,
        number=pr_number,
        url=f"https://github.com/{owner}/{repo}/pull/{pr_number}",
    )
    result = await trigger_pr_review_from_ref(pr_ref, source="dashboard", github_login=login)
    if not result.get("success"):
        raise HTTPException(502, str(result.get("error") or "could not trigger review"))
    return result
