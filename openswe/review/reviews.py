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
from typing import Any, Literal, Self
from urllib.parse import urljoin, urlparse

import httpx2
from fastapi import HTTPException, Response
from langgraph_sdk.errors import NotFoundError
from pydantic import BaseModel, TypeAdapter, ValidationError

from openswe.database import postgres
from openswe.github.app import get_github_app_installation_token
from openswe.github.http import GitHubAppUnavailable, GitHubClient, or_none
from openswe.github.pull_request_diff import (
    build_pr_diff_files,
    fetch_file_versions,
)
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.webhook import trigger_pr_review_from_ref
from openswe.review.assessment_feedback import ASSESSMENTS
from openswe.review.findings import (
    REVIEWER_THREAD_KIND,
    Finding,
    FindingLike,
    comment_ids_for_finding,
    findings_by_thread,
    is_thread_resolved,
)
from openswe.review.session import PullRequestState, ReviewSession, now_ms
from openswe.review.walkthrough import WalkthroughView
from openswe.review_scout.launch import ReviewScoutTarget, ScoutProgress
from openswe.thread_ids import reviewer_thread_id
from openswe.utils.json_types import ThreadLike, thread_metadata
from openswe.utils.thread_ops import langgraph_client, thread_run_error
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

_GITHUB_TIMEOUT = httpx2.Timeout(15.0, connect=5.0)


async def _require_app_token() -> str:
    token = await get_github_app_installation_token()
    if not token:
        raise HTTPException(503, "GitHub App token unavailable")
    return token


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
        "created_at": payload.get("created_at"),
        "merged_at": payload.get("merged_at"),
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


def _check_run_views(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": run.get("name") or "",
            "status": run.get("status") or "",
            "conclusion": run.get("conclusion"),
            "url": run.get("html_url"),
        }
        for run in runs
    ]


async def get_pr_head_sha(owner: str, repo: str, pr_number: int) -> str:
    """Return the PR's current head SHA from GitHub, or "" if unavailable.

    A lightweight alternative to :func:`get_review` for callers that only need to
    detect whether the PR head has moved (e.g. the chat staleness check).
    """
    try:
        async with GitHubClient.as_app() as github:
            pull = github.repo(owner, repo).pull_request(pr_number)
            return await or_none(pull.head_sha()) or ""
    except GitHubAppUnavailable:
        logger.warning(
            "No GitHub App token to read the PR head",
            extra={"repo_full_name": f"{owner}/{repo}", "pr_number": pr_number},
        )
        return ""


PullRequestReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]


class SubmittedReview(BaseModel):
    id: int
    html_url: str
    state: str

    @classmethod
    async def submit(
        cls, pull: PullRequestClient, *, login: str, event: PullRequestReviewEvent, body: str
    ) -> Self:
        """Submit the viewer's review, including every comment in their pending review."""
        pending = await PendingReview.load(pull, login=login)
        if event != "APPROVE" and not body and not (pending and pending.comments):
            raise HTTPException(422, "a comment or change request needs a body or line comments")
        if pending is not None:
            return cls.model_validate(await pull.submit_review(pending.id, event=event, body=body))
        review: dict[str, object] = {"event": event, "commit_id": await pull.head_sha()}
        if body:
            review["body"] = body
        return cls.model_validate(await pull.create_review(review))


DiffSideName = Literal["LEFT", "RIGHT"]


class PendingReviewCommentInput(BaseModel):
    path: str
    line: int
    side: DiffSideName = "RIGHT"
    start_line: int | None = None
    start_side: DiffSideName | None = None
    body: str

    def _spans_lines(self) -> bool:
        return self.start_line is not None and self.start_line != self.line

    def rest_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": self.path,
            "line": self.line,
            "side": self.side,
            "body": self.body,
        }
        if self._spans_lines():
            payload["start_line"] = self.start_line
            payload["start_side"] = self.start_side or self.side
        return payload

    def thread_input(self, review_node_id: str) -> dict[str, object]:
        thread: dict[str, object] = {
            "pullRequestReviewId": review_node_id,
            "path": self.path,
            "line": self.line,
            "side": self.side,
            "body": self.body,
        }
        if self._spans_lines():
            thread["startLine"] = self.start_line
            thread["startSide"] = self.start_side or self.side
        return thread


class PendingReviewComment(BaseModel):
    id: int
    node_id: str
    path: str
    line: int | None = None
    start_line: int | None = None
    side: DiffSideName | None = None
    start_side: DiffSideName | None = None
    body: str


class PendingReview(BaseModel):
    """The viewer's unsubmitted GitHub review on a PR, with its line comments."""

    id: int
    node_id: str
    comments: list[PendingReviewComment] = []

    @classmethod
    async def load(cls, pull: PullRequestClient, *, login: str) -> Self | None:
        """The viewer's pending review, which GitHub shows only to its author."""
        reviews = [_GithubReview.model_validate(raw) for raw in await pull.reviews()]
        pending = next(
            (
                review
                for review in reviews
                if review.state == "PENDING"
                and review.user is not None
                and review.user.login.lower() == login.lower()
            ),
            None,
        )
        if pending is None:
            return None
        # REST omits `line` on pending comments, and GraphQL keeps the side on the thread.
        threads = _REVIEW_THREADS.validate_python(await pull.review_threads())
        comments = [
            PendingReviewComment(
                id=int(comment.fullDatabaseId),
                node_id=comment.id,
                path=thread.path,
                line=thread.line,
                start_line=thread.startLine,
                side=thread.diffSide,
                start_side=thread.startDiffSide,
                body=comment.body,
            )
            for thread in threads
            for comment in thread.comments.nodes
            if comment.pullRequestReview is not None
            and comment.pullRequestReview.fullDatabaseId == str(pending.id)
        ]
        return cls(id=pending.id, node_id=pending.node_id, comments=comments)

    @classmethod
    async def reload(cls, pull: PullRequestClient, *, login: str) -> Self:
        if (pending := await cls.load(pull, login=login)) is None:
            raise HTTPException(502, "GitHub did not keep the pending review")
        return pending

    @classmethod
    async def add_comment(
        cls, pull: PullRequestClient, comment: PendingReviewCommentInput, *, login: str
    ) -> Self:
        """Add a line comment to the viewer's pending review, starting one when there is none."""
        if not comment.body.strip():
            raise HTTPException(422, "comment body is required")
        pending = await cls.load(pull, login=login)
        if pending is None:
            await pull.create_review(
                {"commit_id": await pull.head_sha(), "comments": [comment.rest_payload()]}
            )
        else:
            await pull.add_review_thread(comment.thread_input(pending.node_id))
        return await cls.reload(pull, login=login)

    @classmethod
    async def update_comment(
        cls, pull: PullRequestClient, comment_id: int, body: str, *, login: str
    ) -> Self:
        """Edit a pending comment; REST answers 404 for comments that are not yet submitted."""
        if not body.strip():
            raise HTTPException(422, "comment body is required")
        pending = await cls.load(pull, login=login)
        comment = (
            next((c for c in pending.comments if c.id == comment_id), None) if pending else None
        )
        if comment is None:
            raise HTTPException(404, "comment is not in your pending review")
        await pull.edit_pending_comment(comment.node_id, body)
        return await cls.reload(pull, login=login)

    @classmethod
    async def discard(cls, pull: PullRequestClient, *, login: str) -> bool:
        pending = await cls.load(pull, login=login)
        if pending is None:
            return False
        await pull.delete_review(pending.id)
        return True


class _ReviewAuthor(BaseModel):
    login: str = ""


class _GithubReview(BaseModel):
    id: int
    node_id: str
    state: str
    user: _ReviewAuthor | None = None


class _ThreadReview(BaseModel):
    fullDatabaseId: str


class _ThreadComment(BaseModel):
    id: str
    fullDatabaseId: str
    body: str
    pullRequestReview: _ThreadReview | None = None


class _ThreadComments(BaseModel):
    nodes: list[_ThreadComment] = []


class _ReviewThread(BaseModel):
    path: str
    line: int | None = None
    startLine: int | None = None
    diffSide: DiffSideName | None = None
    startDiffSide: DiffSideName | None = None
    comments: _ThreadComments = _ThreadComments()


_REVIEW_THREADS = TypeAdapter(list[_ReviewThread])


class PostedReviewComment(BaseModel):
    id: int
    html_url: str

    @classmethod
    async def post(cls, pull: PullRequestClient, comment: PendingReviewCommentInput) -> Self:
        """Post one line comment on the PR right away, outside any pending review."""
        if not comment.body.strip():
            raise HTTPException(422, "comment body is required")
        return cls.model_validate(
            await pull.add_review_comment(
                {"commit_id": await pull.head_sha(), **comment.rest_payload()}
            )
        )


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _clean_comment_body(body: str) -> str:
    return _HTML_COMMENT_RE.sub("", body).strip()


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
    async with GitHubClient.as_app() as github:
        repository = github.repo(owner, repo)
        pr_payload = await repository.pull_request(pr_number).pull()
        details = _serialize_pr_details(pr_payload)

        thread = await _reviewer_thread_for(owner, repo, pr_number)
        stored = (
            (await _thread_findings([thread])).get(thread.get("thread_id"), []) if thread else []
        )
        summary = _thread_review_summary(thread, stored) if thread else None
        metadata = thread_metadata(thread) if thread else {}
        if not summary:
            summary = _unreviewed_summary(owner, repo, pr_number, pr_payload)

        head_sha = details["head_sha"] or summary["head_sha"]
        runs = await or_none(repository.check_runs(head_sha)) if head_sha else None
    checks = _check_run_views(runs or [])

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
    walkthrough_error = (
        await _scout_failure(target)
        if walkthrough is None and target is not None and not walkthrough_running
        else None
    )
    walkthrough_progress = (
        await _scout_progress(target) if walkthrough_running and target is not None else None
    )
    thread_id = thread.get("thread_id") if thread else None
    review_error = (
        await _reviewer_failure(thread_id)
        if summary.get("status") == "error" and isinstance(thread_id, str)
        else None
    )
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
        "walkthrough_error": walkthrough_error,
        "walkthrough_progress": (
            walkthrough_progress.model_dump(mode="json") if walkthrough_progress else None
        ),
        "review_error": review_error,
        "walkthrough_scout_thread_id": target.thread_id if target else None,
        "assessment": assessment.model_dump() if assessment else None,
    }


_PREVIEW_FILE_LIMIT = 10


class PreviewFile(BaseModel):
    path: str
    status: str
    additions: int
    deletions: int


class PreviewReply(BaseModel):
    author: str | None = None
    body: str
    url: str | None = None


class PreviewThread(BaseModel):
    thread_id: str | None = None
    author: str | None = None
    body: str
    path: str
    line: int | None = None
    url: str | None = None
    replies: list[PreviewReply] = []


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
    human_input: str


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
    return parsed.model_copy(
        update={
            "body": _clean_comment_body(parsed.body),
            "replies": [
                reply.model_copy(update={"body": _clean_comment_body(reply.body)})
                for reply in parsed.replies
            ],
        }
    )


async def get_pull_request_preview(pull_request: PullRequestClient) -> PullRequestPreview:
    """Description, the largest changed files, and unresolved threads for any PR.

    Unlike ``get_review`` this does not need a reviewer thread, so it answers for
    every PR the viewer can reach. Only file metadata is read — the contents live
    behind ``get_review_diff``, which is far too heavy to open a preview with.

    Read as the viewer rather than the App: the preview only ever shows a PR the
    viewer can already open, unlike the published review a reviewer thread backs.
    """
    repository = pull_request.repo
    owner, repo, pr_number = repository.owner, repository.name, pull_request.number
    pull_payload, raw_files, threads = await asyncio.gather(
        pull_request.pull(), pull_request.files(), pull_request.unresolved_threads()
    )
    pull = _GithubPreviewPull.model_validate(pull_payload)
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
    walkthrough = await WalkthroughView.for_head(owner, repo, pr_number, head_sha)
    # CI reaches GitHub as check runs or as legacy commit statuses, and the PR
    # list counts both — a preview reading only one would contradict the rail.
    raw_checks, raw_statuses = (
        await asyncio.gather(
            or_none(repository.check_runs(head_sha)), or_none(repository.commit_statuses(head_sha))
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
        human_input=walkthrough.human_input if walkthrough else "",
    )


async def get_review_diff(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    """Return the PR's changed files as per-file git patches.

    Uses the App installation token so the diff is available regardless of who
    is viewing the review. The client renders these with pierre's PatchDiff and
    calls :func:`get_review_file_contents` to expand context on demand.
    """
    async with GitHubClient.as_app(timeout=_GITHUB_TIMEOUT) as github:
        diff = await build_pr_diff_files(
            github.http, f"{owner}/{repo}", pr_number, with_contents=False
        )
    files = diff["files"]
    return {
        "files": [
            {
                **{key: value for key, value in f.items() if key not in _CONTENT_KEYS},
                "baseSha": diff["base_sha"],
                "headSha": diff["head_sha"],
            }
            for f in files
        ],
        "total_additions": sum(f["additions"] for f in files),
        "total_deletions": sum(f["deletions"] for f in files),
        "truncated": diff["truncated"],
    }


_CONTENT_KEYS = frozenset({"originalContent", "modifiedContent"})


async def get_review_file_contents(
    owner: str,
    repo: str,
    pr_number: int,
    path: str,
    original_path: str,
    base_sha: str,
    head_sha: str,
) -> dict[str, str | None]:
    """Return one file's contents at the PR's merge base and head."""
    if not all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in (base_sha, head_sha)):
        raise HTTPException(400, "invalid diff revision")
    full_name = f"{owner}/{repo}"
    async with GitHubClient.as_app(timeout=_GITHUB_TIMEOUT) as github:
        return await fetch_file_versions(
            github.http, full_name, path, original_path or path, base_sha, head_sha
        )


# --- PR description image proxy ----------------------------------------------
# PR bodies can embed images hosted on GitHub (user-attachment uploads,
# *.githubusercontent.com). For private repos those URLs require GitHub auth the
# browser doesn't have, so they render broken. We proxy them through the App
# installation token. The host allowlist + per-redirect public-IP check guard
# against SSRF (only GitHub-owned hosts are ever contacted).

_ALLOWED_IMAGE_HOST_SUFFIXES = (".githubusercontent.com",)
# github.com/user-attachments redirects anonymous requests to signed URLs here.
_GITHUB_ASSET_HOSTS = frozenset({"github-production-user-asset-6210df.s3.amazonaws.com"})
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
    return host in _GITHUB_ASSET_HOSTS or any(
        host.endswith(suffix) for suffix in _ALLOWED_IMAGE_HOST_SUFFIXES
    )


def _image_request_headers(url: str, token: str) -> dict[str, str]:
    """Send the token only where it authorizes content.

    github.com answers a bearer token on user-attachments with an HTML page, and
    the signed asset URL it redirects to rejects any second credential.
    """
    host = (urlparse(url).hostname or "").lower()
    headers = {"Accept": "image/*"}
    if any(host.endswith(suffix) for suffix in _ALLOWED_IMAGE_HOST_SUFFIXES):
        headers["Authorization"] = f"Bearer {token}"
    return headers


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


async def _require_image_in_pr(owner: str, repo: str, pr_number: int, url: str) -> None:
    """Bind the requested image to the authorized PR.

    The proxy fetches with the App installation token, which can read every repo
    the App is installed on. Without this check a caller authorized for one repo
    could proxy an image URL from another private repo (IDOR). Only URLs that
    actually appear in this PR's body are allowed.
    """
    async with GitHubClient.as_app() as github:
        pr_payload = await github.repo(owner, repo).pull_request(pr_number).pull()
    body = pr_payload.get("body")
    if not isinstance(body, str) or url not in body:
        raise HTTPException(403, "image not referenced by this PR")


async def proxy_pr_image(owner: str, repo: str, pr_number: int, url: str) -> Response:
    """Stream a GitHub-hosted PR image through the App token.

    The URL must appear in the target PR's body (bound to the authorized
    resource), and every URL (including redirect targets) is validated against
    the GitHub host allowlist and a public-IP check before it is contacted.
    """
    _validate_image_url(url)
    await _require_image_in_pr(owner, repo, pr_number, url)
    # Some image hosts must not see the token, so it cannot ride in a GitHubClient.
    token = await _require_app_token()

    current_url = url
    async with httpx2.AsyncClient(timeout=_GITHUB_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_IMAGE_REDIRECTS + 1):
            async with client.stream(
                "GET", current_url, headers=_image_request_headers(current_url, token)
            ) as response:
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
    html_url: str = ""
    state: Literal["open", "closed"] = "open"
    draft: bool = False
    merged: bool = False
    base: _ScoutRef = _ScoutRef()
    head: _ScoutRef = _ScoutRef()

    @property
    def lifecycle(self) -> PullRequestState:
        if self.merged:
            return "merged"
        if self.state == "closed":
            return "closed"
        return "draft" if self.draft else "open"


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


async def _scout_failure(target: ReviewScoutTarget) -> str | None:
    try:
        return await target.last_failure()
    except Exception:
        logger.warning("Could not read review scout failure", exc_info=True, extra=target.log_extra)
        return None


async def _scout_progress(target: ReviewScoutTarget) -> ScoutProgress | None:
    try:
        return await target.progress()
    except Exception:
        logger.warning(
            "Could not read review scout progress", exc_info=True, extra=target.log_extra
        )
        return None


async def _reviewer_failure(thread_id: str) -> str | None:
    try:
        return await thread_run_error(thread_id)
    except Exception:
        logger.warning(
            "Could not read reviewer failure",
            exc_info=True,
            extra={"reviewer_thread_id": thread_id},
        )
        return None


class ReviewScoutTrigger(BaseModel):
    started: bool
    run_id: str | None = None


async def trigger_review_scout(
    owner: str, repo: str, pr_number: int, login: str
) -> ReviewScoutTrigger:
    """Start the review scout for the PR's current head, or join the one already running.

    Also lists the review in ``login``'s sidebar, where it shows the build's progress.
    """
    async with GitHubClient.as_app() as github:
        pr_payload = await github.repo(owner, repo).pull_request(pr_number).pull()
    target = await _scout_target(owner, repo, pr_number, pr_payload)
    if target is None:
        raise HTTPException(503, "the review scout needs a database and a pull request head")
    # Taken before the scout starts, so a walkthrough it stores quickly still counts as newer.
    requested_at_ms = now_ms()
    if await target.walkthrough() is not None:
        trigger = ReviewScoutTrigger(started=False)
    else:
        trigger = ReviewScoutTrigger(started=True, run_id=await target.start())
    pull = _ScoutPull.model_validate(pr_payload if isinstance(pr_payload, dict) else {})
    try:
        await ReviewSession(owner=owner, repo=repo, pr_number=pr_number, login=login).open(
            title=pull.title,
            url=pull.html_url,
            state=pull.lifecycle,
            workspace=target.workspace_slug,
            walkthrough_ready=not trigger.started,
            requested_at_ms=requested_at_ms,
        )
    except Exception:
        logger.warning(
            "Could not list the review in the sidebar", exc_info=True, extra=target.log_extra
        )
    return trigger


async def trigger_re_review(owner: str, repo: str, pr_number: int, login: str) -> dict[str, Any]:
    from openswe.slack.client import GitHubPrRef

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
