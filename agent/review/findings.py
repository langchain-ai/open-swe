"""Findings storage for the reviewer agent.

Findings live in LangGraph thread metadata under the canonical reviewer thread
for a PR. This file owns the Finding schema and the read/write helpers that
the reviewer's tools and webhook handlers go through.

Why thread metadata: it survives sandbox eviction, is queryable cross-thread
via the langgraph SDK (a future UI lists all reviewer threads by filtering on
``metadata.kind == "reviewer"``), and matches existing patterns the codebase
already uses for durable non-secret run state like ``sandbox_id``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
import weakref
from collections.abc import Callable
from typing import Any, Literal, TypedDict, cast

from langgraph.config import get_config
from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError as LangGraphSDKNotFoundError

logger = logging.getLogger(__name__)
_FINDING_MUTATION_LOCKS: weakref.WeakValueDictionary[tuple[str, int], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


class ReviewerThreadMissingError(RuntimeError):
    """The reviewer thread backing findings storage does not exist.

    Raised instead of the SDK's ``NotFoundError`` so tool wrappers can return a
    structured do-not-retry result: the thread won't appear on retry (evicted,
    eval-mode, or never created), and blind retries burn the whole run.
    """

    def __init__(self, thread_id: str, original: Exception) -> None:
        super().__init__(f"Reviewer thread {thread_id!r} not found: {original}")
        self.thread_id = thread_id


REVIEWER_THREAD_KIND = "reviewer"
REVIEWER_EVAL_PUBLICATION_KEY = "reviewer_eval_publication"

# Suggestions are only useful when the reader can scan them at a glance and
# accept with one click. Anything longer reads as the reviewer rewriting the
# code for the author and clutters the comment. We cap at 4 lines and drop
# longer suggestions; the description still gets posted on its own.
MAX_SUGGESTION_LINES = 4
MAX_FINDING_TITLE_LENGTH = 120
DEFAULT_FINDING_TITLE = "Code review finding"
REVIEW_FINDING_CAP = 6
FINDING_FINGERPRINT_VERSION = 1


def clip_suggestion(suggestion: str | None) -> tuple[str | None, bool]:
    """Return (suggestion_or_none, was_dropped). Drops if over the line cap."""
    if not suggestion:
        return suggestion, False
    if suggestion.count("\n") + 1 > MAX_SUGGESTION_LINES:
        return None, True
    return suggestion, False


def normalize_finding_title(title: str | None, description: str = "") -> str:
    """Return a compact finding title suitable for a review comment headline."""
    raw = title.strip() if isinstance(title, str) else ""
    if not raw and description:
        raw = description.strip().split("\n", 1)[0].strip()
    compact = " ".join(raw.split())
    if not compact:
        return DEFAULT_FINDING_TITLE
    if len(compact) > MAX_FINDING_TITLE_LENGTH:
        return f"{compact[: MAX_FINDING_TITLE_LENGTH - 3].rstrip()}..."
    return compact


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


class Finding(TypedDict):
    """A single review finding.

    Core fields are required for newly-created findings. Legacy and
    publication-only fields remain optional while old thread metadata ages out.
    """

    id: str
    severity: Severity
    confidence: Confidence
    category: str
    title: str
    file: str
    start_line: int | None
    end_line: int | None
    side: DiffSide
    in_diff: bool
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
    github_posted_resolution_comment_ids: list[int]
    last_human_reply_at: str | None
    last_human_reply_author: str | None
    last_human_reply_body: str | None
    last_reconciliation_note: str | None
    resolution_note: str | None
    diff_hunk: str | None
    fingerprint: str
    anchor: FindingAnchor | None
    surface: FindingSurface | None
    interactions: list[FindingInteraction]


class AppendFindingResult(TypedDict):
    finding: Finding
    created: bool


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
    author: str


class ReviewerSlackThread(TypedDict, total=False):
    """Slack thread that initiated this review — used to post a completion reply."""

    channel_id: str
    thread_ts: str


class ReviewerEvalPublication(TypedDict):
    finding_ids: list[str]
    severity_threshold: Severity
    cap: int


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
    title: str | None = None,
    confidence: Confidence = "medium",
    side: DiffSide = "RIGHT",
    suggestion: str | None = None,
    diff_hunk: str | None = None,
    finding_id: str | None = None,
    in_diff: bool = True,
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
    finding: Finding = {
        "id": resolved_id,
        "severity": severity,
        "confidence": confidence,
        "category": category,
        "title": normalize_finding_title(title, description),
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "side": side,
        "in_diff": in_diff,
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
        "github_posted_resolution_comment_ids": [],
        "last_human_reply_at": None,
        "last_human_reply_author": None,
        "last_human_reply_body": None,
        "last_reconciliation_note": None,
        "resolution_note": None,
        "diff_hunk": diff_hunk,
        "fingerprint": _finding_fingerprint(file, side, start_line, end_line, description),
        "anchor": anchor,
        "surface": surface,
        "interactions": [],
    }
    return finding


def _finding_fingerprint(
    file: str,
    side: DiffSide,
    start_line: int | None,
    end_line: int | None,
    description: str,
) -> str:
    payload = {
        "version": FINDING_FINGERPRINT_VERSION,
        "file": file,
        "side": side,
        "start_line": start_line,
        "end_line": end_line,
        "description": " ".join(description.casefold().split()),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"v{FINDING_FINGERPRINT_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


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
    """Fetch the current metadata for a thread.

    Raises :class:`ReviewerThreadMissingError` when the thread does not exist
    (swallowing it as ``{}`` made tools report misleading results like "No
    finding found" instead of the do-not-retry contract). Other transient
    failures still degrade to ``{}``.
    """
    try:
        return await _get_thread_metadata_strict(thread_id)
    except ReviewerThreadMissingError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch thread metadata for %s", thread_id)
        return {}


async def _get_thread_metadata_strict(thread_id: str) -> dict[str, Any]:
    client = get_client()
    try:
        thread = await client.threads.get(thread_id)
    except LangGraphSDKNotFoundError as exc:
        raise ReviewerThreadMissingError(thread_id, exc) from exc
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return metadata if isinstance(metadata, dict) else {}


async def resolve_review_head_sha(thread_id: str, configurable: dict[str, Any]) -> str:
    """Return the current PR head SHA for a reviewer run.

    A push that lands while a reviewer run is in flight is delivered as a queued
    message into that run, whose frozen ``configurable`` still names the head the
    run was created for. The dispatching webhook records the current head in
    thread metadata, so prefer that; fall back to the run's config when metadata
    carries no head (first review, eval, tests).
    """
    config_head = configurable.get("head_sha") if isinstance(configurable, dict) else None
    config_head = config_head if isinstance(config_head, str) else ""
    if not thread_id:
        return config_head
    metadata = await get_thread_metadata(thread_id)
    meta_head = metadata.get("head_sha")
    return meta_head if isinstance(meta_head, str) and meta_head else config_head


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
    """Merge a findings snapshot without dropping concurrently-added records."""
    async with _finding_mutation_lock(thread_id):
        metadata = await _get_thread_metadata_strict(thread_id)
        latest = _coerce_findings_list(metadata.get("findings"))
        incoming_by_id = {finding["id"]: finding for finding in findings}
        merged = [incoming_by_id.pop(finding["id"], finding) for finding in latest]
        merged.extend(incoming_by_id.values())
        await _replace_findings_unlocked(thread_id, merged)


async def _replace_findings_unlocked(thread_id: str, findings: list[Finding]) -> None:
    client = get_client()
    try:
        await client.threads.update(thread_id=thread_id, metadata={"findings": findings})
    except LangGraphSDKNotFoundError as exc:
        raise ReviewerThreadMissingError(thread_id, exc) from exc


def thread_missing_tool_result(exc: ReviewerThreadMissingError) -> dict[str, Any]:
    """Structured tool result for a missing reviewer thread.

    Returned (not raised) so the agent sees an explicit do-not-retry contract
    instead of an empty error blob it retries against.
    """
    return {
        "success": False,
        "error": "thread_not_found",
        "thread_id": exc.thread_id,
        "note": (
            "Reviewer findings storage is unavailable. Do not retry; report the "
            "blocker and include intended findings inline in the final message."
        ),
        "detail": str(exc),
    }


async def mutate_findings(
    thread_id: str,
    mutator: Callable[[list[Finding]], bool],
) -> list[Finding]:
    """Read the latest findings, apply ``mutator`` in place, persist iff changed.

    Centralizes the read-modify-write so every mutation operates on the freshest
    persisted list rather than a stale in-memory snapshot. ``mutator`` edits the
    list in place and returns ``True`` when it changed something; we only write
    on change, so a no-op mutation never clobbers a concurrent update.
    """
    async with _finding_mutation_lock(thread_id):
        metadata = await _get_thread_metadata_strict(thread_id)
        findings = _coerce_findings_list(metadata.get("findings"))
        if mutator(findings):
            await _replace_findings_unlocked(thread_id, findings)
        return findings


def _finding_mutation_lock(thread_id: str) -> asyncio.Lock:
    key = (thread_id, id(asyncio.get_running_loop()))
    lock = _FINDING_MUTATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _FINDING_MUTATION_LOCKS[key] = lock
    return lock


def _current_fingerprint(finding: Finding) -> str:
    return _finding_fingerprint(
        finding["file"],
        finding.get("side", "RIGHT"),
        finding.get("start_line"),
        finding.get("end_line"),
        finding["description"],
    )


async def append_finding(thread_id: str, finding: Finding) -> AppendFindingResult:
    """Persist a finding once and return the canonical stored record."""
    captured: dict[str, Finding] = {}
    fingerprint = _current_fingerprint(finding)

    def _append(findings: list[Finding]) -> bool:
        for existing in findings:
            if existing.get("status", "open") != "open":
                continue
            if _current_fingerprint(existing) == fingerprint:
                captured["finding"] = existing
                return False
        findings.append(finding)
        captured["finding"] = finding
        return True

    await mutate_findings(thread_id, _append)
    persisted = captured["finding"]
    return {"finding": persisted, "created": persisted["id"] == finding["id"]}


async def update_finding_fields(
    thread_id: str,
    finding_id: str,
    updates: dict[str, Any],
) -> Finding | None:
    """Apply field updates to one finding by id and persist."""
    captured: dict[str, Finding] = {}

    def _apply(findings: list[Finding]) -> bool:
        for finding in findings:
            if finding.get("id") == finding_id:
                finding.update(cast(Finding, updates))
                captured["finding"] = finding
                return True
        return False

    await mutate_findings(thread_id, _apply)
    return captured.get("finding")


async def update_finding_surface(
    thread_id: str,
    finding_id: str,
    updates: dict[str, Any],
) -> Finding | None:
    """Apply updates to the nested surface record and legacy GitHub fields."""
    captured: dict[str, Finding] = {}

    def _apply(findings: list[Finding]) -> bool:
        for finding in findings:
            if finding.get("id") != finding_id:
                continue
            surface = _coerce_surface(finding, finding_id)
            surface.update(cast(FindingSurface, updates))
            finding["surface"] = surface
            _sync_legacy_surface_fields(finding, surface)
            captured["finding"] = finding
            return True
        return False

    await mutate_findings(thread_id, _apply)
    return captured.get("finding")


async def append_finding_interaction(
    thread_id: str,
    finding_id: str,
    interaction: FindingInteraction,
) -> Finding | None:
    """Persist a GitHub review-thread interaction on one finding."""
    captured: dict[str, Finding] = {}

    def _apply(findings: list[Finding]) -> bool:
        for finding in findings:
            if finding.get("id") != finding_id:
                continue
            captured["finding"] = finding
            interactions = finding.get("interactions")
            if not isinstance(interactions, list):
                interactions = []
            github_comment_id = interaction.get("github_comment_id")
            if isinstance(github_comment_id, int) and any(
                isinstance(item, dict) and item.get("github_comment_id") == github_comment_id
                for item in interactions
            ):
                return False
            interactions.append(interaction)
            finding["interactions"] = interactions
            return True
        return False

    await mutate_findings(thread_id, _apply)
    return captured.get("finding")


def _coerce_surface(finding: Finding, finding_id: str) -> FindingSurface:
    surface = finding.get("surface")
    coerced: FindingSurface
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
    if "github_review_id" not in coerced:
        coerced["github_review_id"] = finding.get("github_review_id")
    if "github_review_comment_id" not in coerced:
        coerced["github_review_comment_id"] = finding.get("github_review_comment_id")
    if "github_review_thread_id" not in coerced:
        coerced["github_review_thread_id"] = finding.get("github_review_thread_id")
    if "severity_threshold_at_publish" not in coerced:
        coerced["severity_threshold_at_publish"] = None
    if "surfaced_at_sha" not in coerced:
        coerced["surfaced_at_sha"] = None
    if "last_github_sync_at" not in coerced:
        coerced["last_github_sync_at"] = None
    if "last_error" not in coerced:
        coerced["last_error"] = None
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
    head_sha: str | None = None,
    watch: bool | None = None,
    findings: list[Finding] | None = None,
    slack_thread: ReviewerSlackThread | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist reviewer-thread-level metadata.

    Always sets ``kind=reviewer`` so the future UI can list reviewer threads by
    filtering on metadata. Only includes the fields the caller passed in
    (langgraph metadata updates merge rather than overwrite).

    ``head_sha`` records the current PR head the dispatching webhook is acting
    on. A push that lands mid-run is queued into the still-running run, whose
    frozen config can't be updated; persisting the head here lets the reviewer
    tools resolve the live head via ``resolve_review_head_sha``.
    """
    client = get_client()
    metadata: dict[str, Any] = {"kind": REVIEWER_THREAD_KIND}
    if pr is not None:
        metadata["pr"] = pr
    if last_reviewed_sha is not None:
        metadata["last_reviewed_sha"] = last_reviewed_sha
    if head_sha is not None:
        metadata["head_sha"] = head_sha
    if watch is not None:
        metadata["watch"] = watch
    if findings is not None:
        metadata["findings"] = findings
    if slack_thread is not None:
        metadata["slack_thread"] = slack_thread
    if extra:
        metadata.update(extra)
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata)
    except LangGraphSDKNotFoundError as exc:
        raise ReviewerThreadMissingError(thread_id, exc) from exc


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
    cap: int = REVIEW_FINDING_CAP,
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
