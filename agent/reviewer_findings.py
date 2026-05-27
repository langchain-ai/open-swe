"""Findings storage for the reviewer agent.

The GitHub PR is the source of truth for review state. Findings live in
LangGraph thread metadata as a per-run scratchpad: at the start of each
reviewer run we rebuild the findings list from the PR's review threads (by
parsing the ``<!-- open-swe-review-comment ... -->`` marker in each bot
comment) and overwrite the metadata field. During a run the agent mutates
this list via ``add_finding`` / ``update_finding``. At ``publish_review``
time, new findings get posted as inline comments and findings now marked
``resolved``/``dismissed`` have their GitHub threads resolved.

The only durable cross-run state on the reviewer thread (besides the
PR-identity / watch / slack-thread fields) is ``published_comments`` — an
append-only ``{comment_id: {run_id, finding_id}}`` map populated by
``publish_review`` so the GitHub-reaction → LangSmith-feedback flow can map a
👍/👎 reaction back to the LangGraph run that produced the comment, without
scanning findings.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal, TypedDict, cast

from langgraph.config import get_config
from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

REVIEWER_THREAD_KIND = "reviewer"
OPEN_SWE_BOT_AUTHORS = frozenset({"open-swe", "open-swe[bot]"})

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

    Findings reconstructed from existing PR review threads carry
    ``github_review_comment_id`` and ``github_review_thread_ids`` so that
    ``update_finding(status="resolved")`` can resolve the GitHub thread and
    ``reply_to_finding_thread`` can post a reply. New findings (created via
    ``add_finding`` during the current run) leave both empty.
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
    github_review_comment_id: int | None
    github_review_thread_ids: list[str]
    diff_hunk: str | None


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
    status: FindingStatus = "open",
    github_review_comment_id: int | None = None,
    github_review_thread_ids: list[str] | None = None,
) -> Finding:
    """Construct a fully-populated ``Finding`` ready to persist."""
    return {
        "id": finding_id or new_finding_id(),
        "severity": severity,
        "confidence": confidence,
        "category": category,
        "file": file,
        "start_line": start_line,
        "end_line": end_line,
        "side": side,
        "description": description,
        "suggestion": suggestion,
        "status": status,
        "first_seen_sha": sha,
        "last_confirmed_sha": sha,
        "github_review_comment_id": github_review_comment_id,
        "github_review_thread_ids": list(github_review_thread_ids or []),
        "diff_hunk": diff_hunk,
    }


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


def is_open_swe_bot_comment(comment: dict[str, Any]) -> bool:
    return comment.get("author") in OPEN_SWE_BOT_AUTHORS


def findings_from_pr_threads(
    review_threads: list[dict[str, Any]],
    *,
    head_sha: str,
) -> list[Finding]:
    """Rebuild the findings list from the PR's review threads.

    Walks each thread, finds the first bot-authored comment carrying an
    ``open-swe-review-comment`` marker, and materializes a ``Finding`` from
    that marker. Threads with no bot/marker comment are ignored — those are
    other reviewers' comments and the agent reads them from the
    ``<pr_review_threads>`` block in the prompt.

    Multiple threads can share a marker id (legacy from when reconciliation
    was buggy and duplicated comments). In that case they're collapsed into a
    single finding with all thread ids attached, so resolving the finding
    closes every duplicate.
    """
    from .reviewer_publish import parse_review_comment_marker

    findings_by_id: dict[str, Finding] = {}
    for thread in review_threads:
        if not isinstance(thread, dict):
            continue
        thread_node_id = thread.get("id")
        comments = thread.get("comments") or []
        if not isinstance(comments, list) or not isinstance(thread_node_id, str):
            continue
        marker = None
        bot_comment: dict[str, Any] | None = None
        for comment in comments:
            if not isinstance(comment, dict) or not is_open_swe_bot_comment(comment):
                continue
            body = comment.get("body")
            if not isinstance(body, str):
                continue
            candidate = parse_review_comment_marker(body)
            if candidate is not None:
                marker = candidate
                bot_comment = comment
                break
        if marker is None or bot_comment is None:
            continue

        finding_id = marker["id"]
        is_terminal = bool(thread.get("is_resolved") or thread.get("is_outdated"))
        status: FindingStatus = "resolved" if is_terminal else "open"
        comment_id = bot_comment.get("id") if isinstance(bot_comment.get("id"), int) else None

        existing = findings_by_id.get(finding_id)
        if existing is not None:
            thread_ids = list(existing.get("github_review_thread_ids") or [])
            if thread_node_id not in thread_ids:
                thread_ids.append(thread_node_id)
            existing["github_review_thread_ids"] = thread_ids
            # If any duplicate is still open the finding is open; only mark
            # resolved when every known thread for this id is terminal.
            if status == "open":
                existing["status"] = "open"
            continue

        body = bot_comment.get("body") if isinstance(bot_comment.get("body"), str) else ""
        description = _description_from_comment_body(body)

        findings_by_id[finding_id] = new_finding(
            severity="medium",
            confidence="medium",
            category="correctness",
            file=marker["file_path"],
            start_line=marker["start_line"],
            end_line=marker["end_line"],
            description=description,
            sha=head_sha,
            side=marker["side"],
            finding_id=finding_id,
            status=status,
            github_review_comment_id=comment_id,
            github_review_thread_ids=[thread_node_id],
        )
    return list(findings_by_id.values())


def _description_from_comment_body(body: str) -> str:
    """Strip the marker prefix from a posted comment body so it reads as a
    finding description again. Drops the ``<!-- open-swe-review-comment ... -->``
    header and any leading blank lines."""
    import re

    stripped = re.sub(
        r"^\s*<!--\s*open-swe-review-comment\s+.*?-->\s*\n*",
        "",
        body,
        count=1,
        flags=re.DOTALL,
    )
    return stripped.strip()


def get_published_comments_map(metadata: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Return the ``published_comments`` map keyed by comment id (as str).

    The map persists across runs and records which LangGraph run produced
    each published inline comment. It is the source consulted by the
    GitHub-reaction → LangSmith-feedback flow.
    """
    value = metadata.get("published_comments")
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, entry in value.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        run_id = entry.get("run_id")
        finding_id = entry.get("finding_id")
        if not isinstance(run_id, str) or not isinstance(finding_id, str):
            continue
        out[key] = {"run_id": run_id, "finding_id": finding_id}
    return out


async def record_published_comments(
    thread_id: str,
    entries: dict[int, dict[str, str]],
) -> None:
    """Append ``{comment_id: {run_id, finding_id}}`` entries to thread metadata.

    Idempotent: re-recording an existing comment id overwrites the entry.
    Uses ``threads.update`` so existing metadata fields are preserved.
    """
    if not entries:
        return
    metadata = await get_thread_metadata(thread_id)
    current = get_published_comments_map(metadata)
    for comment_id, payload in entries.items():
        if not isinstance(comment_id, int):
            continue
        current[str(comment_id)] = payload
    client = get_client()
    await client.threads.update(thread_id=thread_id, metadata={"published_comments": current})
