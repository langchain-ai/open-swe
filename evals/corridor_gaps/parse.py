import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from evals.corridor_gaps.github import CORRIDOR_LOGIN, OSWE_LOGIN, PrBundle, Review, ReviewComment

Severity = Literal["critical", "high", "medium", "low"]

_OSWE_SEVERITY: dict[str, Severity] = {"🔴": "critical", "🟠": "high", "🟡": "medium", "🔵": "low"}
_OSWE_MARKER = re.compile(r"<!-- open-swe-review-comment (\{.*?\}) -->")
_OSWE_TITLE = re.compile(r"^(🔴|🟠|🟡|🔵)\s*\*\*(.+?)\*\*", re.MULTILINE)
_FOUND = re.compile(r"found (\d+) potential issue")
_RISK = re.compile(r"Risk: (\d)/5")
_TRACE = re.compile(r"\[View Open SWE trace\]\((\S+?)\)")
_CORRIDOR_FINDING = re.compile(
    r"\((https://app\.corridor\.dev/projects/[^/]+/findings/([0-9a-f-]+))\)"
)
_CORRIDOR_BOILERPLATE = re.compile(
    r"\n*(For more details, see the \[finding in Corridor\].*|\*\*Provide feedback\*\*.*)$",
    re.MULTILINE,
)


class _OsweMarker(BaseModel):
    id: str


class Reply(BaseModel):
    login: str
    created_at: datetime
    body: str


class OsweFinding(BaseModel):
    comment_id: int
    finding_id: str | None
    commit_sha: str
    created_at: datetime
    path: str
    line: int | None
    severity: Severity | None
    title: str | None
    body: str
    url: str
    replies: list[Reply]


class OsweReview(BaseModel):
    review_id: int
    commit_sha: str | None
    submitted_at: datetime | None
    outcome: Literal["findings", "no_issues", "update"]
    finding_count: int | None
    risk: int | None
    trace_url: str | None
    url: str


class CorridorFinding(BaseModel):
    comment_id: int
    corridor_finding_id: str | None
    corridor_finding_url: str | None
    commit_sha: str
    created_at: datetime
    path: str
    start_line: int | None
    line: int | None
    side: str | None
    diff_hunk: str
    summary: str | None
    body: str
    url: str
    replies: list[Reply]


def _login(item: Review | ReviewComment) -> str:
    return item.user.login if item.user else ""


def _replies(bundle: PrBundle, comment_id: int) -> list[Reply]:
    return [
        Reply(login=_login(c), created_at=c.created_at, body=c.body)
        for c in sorted(bundle.comments, key=lambda c: c.created_at)
        if c.in_reply_to_id == comment_id
    ]


def oswe_findings(bundle: PrBundle) -> list[OsweFinding]:
    out: list[OsweFinding] = []
    for c in bundle.comments:
        if _login(c) != OSWE_LOGIN or c.in_reply_to_id is not None:
            continue
        marker = _OSWE_MARKER.search(c.body)
        title = _OSWE_TITLE.search(c.body)
        out.append(
            OsweFinding(
                comment_id=c.id,
                finding_id=_OsweMarker.model_validate_json(marker.group(1)).id if marker else None,
                commit_sha=c.original_commit_id,
                created_at=c.created_at,
                path=c.path,
                line=c.original_line or c.line,
                severity=_OSWE_SEVERITY[title.group(1)] if title else None,
                title=title.group(2) if title else None,
                body=_OSWE_MARKER.sub("", c.body).strip(),
                url=c.html_url,
                replies=_replies(bundle, c.id),
            )
        )
    return sorted(out, key=lambda f: f.created_at)


def oswe_reviews(bundle: PrBundle) -> list[OsweReview]:
    out: list[OsweReview] = []
    for r in bundle.reviews:
        if _login(r) != OSWE_LOGIN:
            continue
        found = _FOUND.search(r.body)
        risk = _RISK.search(r.body)
        trace = _TRACE.search(r.body)
        if found:
            outcome: Literal["findings", "no_issues", "update"] = "findings"
        elif "No issues found" in r.body:
            outcome = "no_issues"
        else:
            outcome = "update"
        out.append(
            OsweReview(
                review_id=r.id,
                commit_sha=r.commit_id,
                submitted_at=r.submitted_at,
                outcome=outcome,
                finding_count=int(found.group(1))
                if found
                else (0 if outcome == "no_issues" else None),
                risk=int(risk.group(1)) if risk else None,
                trace_url=trace.group(1) if trace else None,
                url=r.html_url,
            )
        )
    return out


def corridor_findings(bundle: PrBundle) -> list[CorridorFinding]:
    summaries = {
        r.id: r.body.strip() or None for r in bundle.reviews if _login(r) == CORRIDOR_LOGIN
    }
    out: list[CorridorFinding] = []
    for c in bundle.comments:
        if _login(c) != CORRIDOR_LOGIN or c.in_reply_to_id is not None:
            continue
        link = _CORRIDOR_FINDING.search(c.body)
        out.append(
            CorridorFinding(
                comment_id=c.id,
                corridor_finding_id=link.group(2) if link else None,
                corridor_finding_url=link.group(1) if link else None,
                commit_sha=c.original_commit_id,
                created_at=c.created_at,
                path=c.path,
                start_line=c.original_start_line or c.start_line,
                line=c.original_line or c.line,
                side=c.side,
                diff_hunk=c.diff_hunk,
                summary=summaries.get(c.pull_request_review_id)
                if c.pull_request_review_id
                else None,
                body=_CORRIDOR_BOILERPLATE.sub("", c.body).strip(),
                url=c.html_url,
                replies=_replies(bundle, c.id),
            )
        )
    return sorted(out, key=lambda f: f.created_at)


def commit_order(bundle: PrBundle) -> dict[str, int]:
    return {c.sha: i for i, c in enumerate(bundle.commits, 1)}
