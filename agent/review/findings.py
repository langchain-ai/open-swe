"""Findings storage for the reviewer agent.

Findings live in LangGraph thread metadata under the canonical reviewer thread
for a PR. This file owns the Finding schema and the read/write helpers that
the reviewer's tools and webhook handlers go through.

Why thread metadata: it survives sandbox eviction, is queryable cross-thread
via the langgraph SDK (a future UI lists all reviewer threads by filtering on
``metadata.kind == "reviewer"``), and matches existing patterns the codebase
already uses for durable non-secret run state like ``sandbox_id``.
"""

import asyncio
import hashlib
import json
import logging
import uuid
import weakref
from collections.abc import Callable, Mapping
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
SurfaceState = Literal["not_surfaced", "surfaced", "resolve_pending", "resolved"]
InteractionKind = Literal["human_reply", "bot_reply"]

SEVERITY_ORDER: dict[Severity, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

# Surface states only ever move forward, so normalization of a legacy record can
# reconcile contradictory leftovers by taking the furthest-along state.
SURFACE_STATE_ORDER: dict[SurfaceState, int] = {
    "not_surfaced": 0,
    "surfaced": 1,
    "resolve_pending": 2,
    "resolved": 3,
}

# Confidence is recorded on every finding for post-hoc calibration analysis
# but does not gate publication — the system prompt's defensibility bar is
# the discipline.


class Finding(TypedDict):
    """A single review finding.

    Where a finding surfaced on GitHub is recorded exactly once, by the
    ``github_*_ids`` lists plus ``surface_state``. Records persisted by older
    revisions carry flat singulars and a nested ``surface`` record instead;
    :func:`coerce_finding` folds those into the canonical fields on read, so
    nothing outside this module ever sees the legacy shape.
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
    github_review_run_id: str | None
    github_review_comment_ids: list[int]
    github_review_thread_ids: list[str]
    github_resolved_thread_ids: list[str]
    github_posted_resolution_comment_ids: list[int]
    surface_state: SurfaceState
    last_human_reply_at: str | None
    last_human_reply_author: str | None
    last_human_reply_body: str | None
    last_reconciliation_note: str | None
    resolution_note: str | None
    diff_hunk: str | None
    fingerprint: str
    interactions: "list[FindingInteraction]"


class AppendFindingResult(TypedDict):
    finding: Finding
    created: bool


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
        "github_review_run_id": None,
        "github_review_comment_ids": [],
        "github_review_thread_ids": [],
        "github_resolved_thread_ids": [],
        "github_posted_resolution_comment_ids": [],
        "surface_state": "not_surfaced",
        "last_human_reply_at": None,
        "last_human_reply_author": None,
        "last_human_reply_body": None,
        "last_reconciliation_note": None,
        "resolution_note": None,
        "diff_hunk": diff_hunk,
        "fingerprint": _finding_fingerprint(file, side, start_line, end_line, description),
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


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


# Read accessors take a plain mapping so callers holding a raw persisted record
# (dashboard serializers, usage rollups) can use them without a cast.
FindingLike = Mapping[str, Any]


def comment_ids_for_finding(finding: FindingLike) -> list[int]:
    """GitHub review comment ids this finding was posted as, oldest first."""
    return _int_list(finding.get("github_review_comment_ids"))


def thread_ids_for_finding(finding: FindingLike) -> list[str]:
    """GitHub review thread node ids this finding lives in, oldest first."""
    return _str_list(finding.get("github_review_thread_ids"))


def resolved_thread_ids_for_finding(finding: FindingLike) -> list[str]:
    return _str_list(finding.get("github_resolved_thread_ids"))


def posted_resolution_comment_ids_for_finding(finding: FindingLike) -> list[int]:
    return _int_list(finding.get("github_posted_resolution_comment_ids"))


def review_id_for_finding(finding: FindingLike) -> int | None:
    review_id = finding.get("github_review_id")
    return review_id if isinstance(review_id, int) and not isinstance(review_id, bool) else None


def surface_state_of(finding: FindingLike) -> SurfaceState:
    state = finding.get("surface_state")
    return state if state in SURFACE_STATE_ORDER else "not_surfaced"


def is_surfaced(finding: FindingLike) -> bool:
    """True once this finding has been posted to the PR."""
    return surface_state_of(finding) != "not_surfaced"


def is_thread_resolved(finding: FindingLike) -> bool:
    """True once every GitHub review thread for this finding is resolved."""
    return surface_state_of(finding) == "resolved"


def mark_surfaced(finding: Finding) -> bool:
    """Record that the finding is now on GitHub. Returns True when it changed."""
    if is_surfaced(finding):
        return False
    finding["surface_state"] = "surfaced"
    return True


def set_surface_state(finding: Finding, state: SurfaceState) -> bool:
    """Set the surface state outright. Returns True when it changed."""
    if surface_state_of(finding) == state:
        return False
    finding["surface_state"] = state
    return True


def _legacy_surface_state(record: dict[str, Any], surface: dict[str, Any]) -> SurfaceState:
    if record.get("github_thread_resolved") is True:
        return "resolved"
    if (
        _int_list(record.get("github_review_comment_ids"))
        or _str_list(record.get("github_review_thread_ids"))
        or isinstance(record.get("github_review_id"), int)
        or isinstance(surface.get("github_review_comment_id"), int)
        or isinstance(surface.get("github_review_thread_id"), str)
    ):
        return "surfaced"
    return "not_surfaced"


def _normalize_publication_identity(record: dict[str, Any]) -> None:
    """Fold legacy GitHub-identity fields into the canonical ones, in place.

    Records written before the identity fields were unified carry flat
    singulars (``github_review_comment_id``, …), a ``github_thread_resolved``
    flag and a nested ``surface`` record. Every read passes through here, so
    the rest of the codebase — and every subsequent write — only ever deals
    with the lists plus ``surface_state``.
    """
    surface = record.pop("surface", None)
    surface = surface if isinstance(surface, dict) else {}
    record.pop("anchor", None)

    comment_ids = _int_list(record.get("github_review_comment_ids"))
    for legacy_comment_id in (
        record.pop("github_review_comment_id", None),
        surface.get("github_review_comment_id"),
    ):
        if isinstance(legacy_comment_id, int) and legacy_comment_id not in comment_ids:
            comment_ids.insert(0, legacy_comment_id)
    record["github_review_comment_ids"] = comment_ids

    thread_ids = _str_list(record.get("github_review_thread_ids"))
    for legacy_thread_id in (
        record.pop("github_review_thread_id", None),
        surface.get("github_review_thread_id"),
    ):
        if isinstance(legacy_thread_id, str) and legacy_thread_id not in ("", *thread_ids):
            thread_ids.insert(0, legacy_thread_id)
    record["github_review_thread_ids"] = thread_ids

    if not isinstance(record.get("github_review_id"), int):
        legacy_review_id = surface.get("github_review_id")
        record["github_review_id"] = legacy_review_id if isinstance(legacy_review_id, int) else None

    states: list[SurfaceState] = [_legacy_surface_state(record, surface)]
    for candidate in (record.get("surface_state"), surface.get("state")):
        if candidate in SURFACE_STATE_ORDER:
            states.append(cast(SurfaceState, candidate))
    record.pop("github_thread_resolved", None)
    record["surface_state"] = max(states, key=lambda state: SURFACE_STATE_ORDER[state])


def coerce_finding(value: Any) -> Finding | None:
    """Normalize one persisted record into a canonical ``Finding``.

    Returns ``None`` when the value isn't a finding record at all.
    """
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("id"), str):
        return None
    _normalize_publication_identity(value)
    return cast(Finding, value)


def coerce_findings(value: Any) -> list[Finding]:
    """Normalize a persisted findings blob into canonical ``Finding`` records."""
    if not isinstance(value, list):
        return []
    out: list[Finding] = []
    for entry in value:
        finding = coerce_finding(entry)
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
    return coerce_findings(metadata.get("findings"))


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
        latest = coerce_findings(metadata.get("findings"))
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
    from agent.dashboard.agent_usage import record_reviewer_finding_state

    results = await asyncio.gather(
        *(record_reviewer_finding_state(thread_id, finding) for finding in findings),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, Exception)]
    if failures:
        logger.debug("Failed to update reviewer usage telemetry: %s", failures[0])


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
        findings = coerce_findings(metadata.get("findings"))
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
