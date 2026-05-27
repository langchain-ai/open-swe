"""Findings storage for the reviewer agent.

Findings live in LangGraph thread metadata under the canonical reviewer thread
for a PR. This file owns the Finding schema and the read/write helpers that
the reviewer's tools and webhook handlers go through.

Why thread metadata: it survives sandbox eviction, is queryable cross-thread
via the langgraph SDK (a future UI lists all reviewer threads by filtering on
``metadata.kind == "reviewer"``), and matches existing patterns the codebase
already uses for ``sandbox_id``, ``github_token_encrypted``, etc.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal, TypedDict, cast

from langgraph.config import get_config
from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

REVIEWER_THREAD_KIND = "reviewer"

# Suggestions are only useful when the reader can scan them at a glance and
# accept with one click. Anything longer reads as the reviewer rewriting the
# code for the author and clutters the comment. We cap at 4 lines and drop
# longer suggestions; the description still gets posted on its own.
MAX_SUGGESTION_LINES = 4


def clip_suggestion(suggestion: str | None) -> tuple[str | None, bool]:
    """Return (suggestion_or_none, was_dropped). Drops if over the line cap."""
    if not suggestion:
        return suggestion, False
    if suggestion.count("\n") + 1 > MAX_SUGGESTION_LINES:
        return None, True
    return suggestion, False


Severity = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
FindingStatus = Literal["open", "resolved", "dismissed"]
DiffSide = Literal["LEFT", "RIGHT"]
SurfaceState = Literal["not_surfaced", "surfaced", "resolve_pending", "resolved", "error"]
InteractionKind = Literal["human_reply", "bot_reply"]

SEVERITY_ORDER: dict[Severity, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Confidence is recorded on every finding for post-hoc calibration analysis
# but does not gate publication — the system prompt's defensibility bar is
# the discipline.


class Finding(TypedDict, total=False):
    """A single review finding.

    All fields are optional at the TypedDict level so partial updates are
    representable, but ``new_finding`` always returns a fully populated dict.
    """

    id: str
    severity: Severity
    confidence: Confidence
    category: str
    file: str
    start_line: int | None
    end_line: int | None
    side: DiffSide
    description: str
    suggestion: str | None
    status: FindingStatus
    first_seen_sha: str
    last_confirmed_sha: str
    github_review_id: int | None
    github_review_comment_id: int | None
    github_review_comment_ids: list[int]
    github_review_thread_id: str | None
    github_review_thread_ids: list[str]
    github_review_run_id: str | None
    github_thread_resolved: bool
    github_resolved_thread_ids: list[str]
    last_human_reply_at: str | None
    last_human_reply_author: str | None
    last_human_reply_body: str | None
    last_reconciliation_note: str | None
    diff_hunk: str | None
    fingerprint: str
    anchor: FindingAnchor
    surface: FindingSurface
    interactions: list[FindingInteraction]


class FindingAnchor(TypedDict):
    file: str
    start_line: int | None
    end_line: int | None
    side: DiffSide


class FindingSurface(TypedDict, total=False):
    finding_id: str
    state: SurfaceState
    github_review_id: int | None
    github_review_comment_id: int | None
    github_review_thread_id: str | None
    severity_threshold_at_publish: Severity | None
    surfaced_at_sha: str | None
    last_github_sync_at: str | None
    last_error: str | None


class FindingInteraction(TypedDict, total=False):
    kind: InteractionKind
    github_comment_id: int | None
    github_parent_comment_id: int | None
    author: str
    body: str
    created_at: str
    needs_reassessment: bool


class ReviewerPRMeta(TypedDict, total=False):
    """PR identity stored on reviewer thread metadata, used by the UI."""

    owner: str
    name: str
    number: int
    url: str
    title: str
    head_ref: str
    base_ref: str


class ReviewerSlackThread(TypedDict, total=False):
    """Slack thread that initiated this review — used to post a completion reply."""

    channel_id: str
    thread_ts: str


def new_finding_id() -> str:
    """Return a stable, short, URL-friendly finding id (``f_<hex>``)."""
    return f"f_{uuid.uuid4().hex[:10]}"


def new_finding(
    *,
    severity: Severity,
    category: str,
    file: str,
    start_line: int | None,
    end_line: int | None,
    description: str,
    sha: str,
    confidence: Confidence = "medium",
    side: DiffSide = "RIGHT",
    suggestion: str | None = None,
    diff_hunk: str | None = None,
    finding_id: str | None = None,
) -> Finding:
    """Construct a fully-populated ``Finding`` ready to persist."""
    resolved_id = finding_id or new_finding_id()
    anchor: FindingAnchor = {
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "side": side,
    }
    surface: FindingSurface = {
        "finding_id": resolved_id,
        "state": "not_surfaced",
        "github_review_id": None,
        "github_review_comment_id": None,
        "github_review_thread_id": None,
        "severity_threshold_at_publish": None,
        "surfaced_at_sha": None,
        "last_github_sync_at": None,
        "last_error": None,
    }
    return {
        "id": resolved_id,
        "severity": severity,
        "confidence": confidence,
        "category": category,
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "side": side,
        "description": description,
        "suggestion": suggestion,
        "status": "open",
        "first_seen_sha": sha,
        "last_confirmed_sha": sha,
        "github_review_id": None,
        "github_review_comment_id": None,
        "github_review_comment_ids": [],
        "github_review_thread_id": None,
        "github_review_thread_ids": [],
        "github_review_run_id": None,
        "github_thread_resolved": False,
        "github_resolved_thread_ids": [],
        "last_human_reply_at": None,
        "last_human_reply_author": None,
        "last_human_reply_body": None,
        "last_reconciliation_note": None,
        "diff_hunk": diff_hunk,
        "fingerprint": _finding_fingerprint(file, start_line, end_line, description),
        "anchor": anchor,
        "surface": surface,
        "interactions": [],
    }


def _finding_fingerprint(
    file: str,
    start_line: int | None,
    end_line: int | None,
    description: str,
) -> str:
    normalized_description = " ".join(description.strip().lower().split())
    return f"{file}:{start_line or ''}:{end_line or ''}:{normalized_description[:160]}"


def _coerce_finding(value: Any) -> Finding | None:
    if not isinstance(value, dict):
        return None
    if "id" not in value or not isinstance(value["id"], str):
        return None
    return cast(Finding, value)


def _coerce_findings_list(value: Any) -> list[Finding]:
    if not isinstance(value, list):
        return []
    out: list[Finding] = []
    for entry in value:
        finding = _coerce_finding(entry)
        if finding is not None:
            out.append(finding)
    return out


def get_thread_id_from_runtime() -> str:
    """Return the thread id from the current LangGraph runnable config."""
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        msg = "No thread_id available in runtime config"
        raise RuntimeError(msg)
    return thread_id


async def get_thread_metadata(thread_id: str) -> dict[str, Any]:
    """Fetch the current metadata for a thread. Returns ``{}`` on miss."""
    client = get_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch thread metadata for %s", thread_id)
        return {}
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return metadata if isinstance(metadata, dict) else {}


async def list_findings(thread_id: str) -> list[Finding]:
    """Return all findings persisted on the reviewer thread."""
    metadata = await get_thread_metadata(thread_id)
    return _coerce_findings_list(metadata.get("findings"))


async def get_finding(thread_id: str, finding_id: str) -> Finding | None:
    """Return one finding by id, or ``None`` if not present."""
    findings = await list_findings(thread_id)
    for finding in findings:
        if finding.get("id") == finding_id:
            return finding
    return None


async def replace_findings(thread_id: str, findings: list[Finding]) -> None:
    """Overwrite the findings list on a thread's metadata."""
    client = get_client()
    await client.threads.update(thread_id=thread_id, metadata={"findings": findings})


async def append_finding(thread_id: str, finding: Finding) -> Finding:
    """Append a finding and persist the new list."""
    findings = await list_findings(thread_id)
    findings.append(finding)
    await replace_findings(thread_id, findings)
    return finding


async def update_finding_fields(
    thread_id: str,
    finding_id: str,
    updates: dict[str, Any],
) -> Finding | None:
    """Apply field updates to one finding by id and persist."""
    findings = await list_findings(thread_id)
    updated: Finding | None = None
    for finding in findings:
        if finding.get("id") == finding_id:
            finding.update(updates)
            updated = finding
            break
    if updated is None:
        return None
    await replace_findings(thread_id, findings)
    return updated


async def update_finding_surface(
    thread_id: str,
    finding_id: str,
    updates: dict[str, Any],
) -> Finding | None:
    """Apply updates to the nested surface record and legacy GitHub fields."""
    findings = await list_findings(thread_id)
    updated: Finding | None = None
    for finding in findings:
        if finding.get("id") != finding_id:
            continue
        surface = _coerce_surface(finding, finding_id)
        surface.update(updates)
        finding["surface"] = surface
        _sync_legacy_surface_fields(finding, surface)
        updated = finding
        break
    if updated is None:
        return None
    await replace_findings(thread_id, findings)
    return updated


async def append_finding_interaction(
    thread_id: str,
    finding_id: str,
    interaction: FindingInteraction,
) -> Finding | None:
    """Persist a GitHub review-thread interaction on one finding."""
    findings = await list_findings(thread_id)
    updated: Finding | None = None
    for finding in findings:
        if finding.get("id") != finding_id:
            continue
        interactions = finding.get("interactions")
        if not isinstance(interactions, list):
            interactions = []
        github_comment_id = interaction.get("github_comment_id")
        if isinstance(github_comment_id, int) and any(
            isinstance(item, dict) and item.get("github_comment_id") == github_comment_id
            for item in interactions
        ):
            updated = finding
            break
        interactions.append(interaction)
        finding["interactions"] = interactions
        updated = finding
        break
    if updated is None:
        return None
    await replace_findings(thread_id, findings)
    return updated


def _coerce_surface(finding: Finding, finding_id: str) -> FindingSurface:
    surface = finding.get("surface")
    if isinstance(surface, dict):
        coerced = cast(FindingSurface, dict(surface))
    else:
        coerced = {"finding_id": finding_id}
    if not coerced.get("state"):
        if finding.get("github_thread_resolved"):
            coerced["state"] = "resolved"
        elif isinstance(finding.get("github_review_comment_id"), int):
            coerced["state"] = "surfaced"
        else:
            coerced["state"] = "not_surfaced"
    coerced.setdefault("github_review_id", finding.get("github_review_id"))
    coerced.setdefault("github_review_comment_id", finding.get("github_review_comment_id"))
    coerced.setdefault("github_review_thread_id", finding.get("github_review_thread_id"))
    coerced.setdefault("severity_threshold_at_publish", None)
    coerced.setdefault("surfaced_at_sha", None)
    coerced.setdefault("last_github_sync_at", None)
    coerced.setdefault("last_error", None)
    return coerced


def _sync_legacy_surface_fields(finding: Finding, surface: FindingSurface) -> None:
    review_id = surface.get("github_review_id")
    if isinstance(review_id, int) or review_id is None:
        finding["github_review_id"] = review_id
    comment_id = surface.get("github_review_comment_id")
    if isinstance(comment_id, int) or comment_id is None:
        finding["github_review_comment_id"] = comment_id
    thread_id = surface.get("github_review_thread_id")
    if isinstance(thread_id, str) or thread_id is None:
        finding["github_review_thread_id"] = thread_id
    if surface.get("state") == "resolved":
        finding["github_thread_resolved"] = True


async def set_reviewer_thread_metadata(
    thread_id: str,
    *,
    pr: ReviewerPRMeta | None = None,
    last_reviewed_sha: str | None = None,
    watch: bool | None = None,
    findings: list[Finding] | None = None,
    slack_thread: ReviewerSlackThread | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist reviewer-thread-level metadata.

    Always sets ``kind=reviewer`` so the future UI can list reviewer threads by
    filtering on metadata. Only includes the fields the caller passed in
    (langgraph metadata updates merge rather than overwrite).
    """
    client = get_client()
    metadata: dict[str, Any] = {"kind": REVIEWER_THREAD_KIND}
    if pr is not None:
        metadata["pr"] = pr
    if last_reviewed_sha is not None:
        metadata["last_reviewed_sha"] = last_reviewed_sha
    if watch is not None:
        metadata["watch"] = watch
    if findings is not None:
        metadata["findings"] = findings
    if slack_thread is not None:
        metadata["slack_thread"] = slack_thread
    if extra:
        metadata.update(extra)
    await client.threads.update(thread_id=thread_id, metadata=metadata)


def get_thread_watch_flag(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("watch"))


def get_thread_last_reviewed_sha(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("last_reviewed_sha")
    return value if isinstance(value, str) and value else None


def get_thread_pr_meta(metadata: dict[str, Any]) -> ReviewerPRMeta | None:
    pr = metadata.get("pr")
    if not isinstance(pr, dict):
        return None
    return cast(ReviewerPRMeta, pr)


def get_thread_slack_ref(metadata: dict[str, Any]) -> ReviewerSlackThread | None:
    slack_thread = metadata.get("slack_thread")
    if not isinstance(slack_thread, dict):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return None
    if not channel_id or not thread_ts:
        return None
    return cast(ReviewerSlackThread, slack_thread)


def filter_findings_for_publish(
    findings: list[Finding],
    *,
    severity_threshold: Severity = "medium",
    cap: int = 4,
) -> list[Finding]:
    """Return findings to surface to GitHub.

    - status must be ``open``
    - severity must be at or above ``severity_threshold``
    - sorted by severity descending, then file/start_line for stable ordering
    - capped at ``cap`` to avoid review spam
    """
    severity_rank = SEVERITY_ORDER[severity_threshold]
    eligible = [
        finding
        for finding in findings
        if finding.get("status", "open") == "open"
        and SEVERITY_ORDER.get(finding.get("severity", "low"), 0) >= severity_rank
    ]
    eligible.sort(
        key=lambda f: (
            -SEVERITY_ORDER.get(f.get("severity", "low"), 0),
            f.get("file", ""),
            f.get("start_line") or 0,
        )
    )
    return eligible[:cap]
