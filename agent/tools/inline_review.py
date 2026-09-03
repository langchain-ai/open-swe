"""Tools for the self-review of a PR this thread authored.

The findings never reach GitHub: they are attached to the authoring thread and
rendered in the dashboard's review panel, and the agent acts on them in the
same run.
"""

import logging
import posixpath
import shlex
from typing import Any, cast

from langgraph.config import get_config

from agent.review.diff import changed_files, materialize_review_diff
from agent.review.findings import clip_suggestion, normalize_finding_title
from agent.review.inline_review import (
    MAX_INLINE_FINDINGS,
    REVIEWS,
    Confidence,
    Disposition,
    InlineFinding,
    InlineReview,
    Severity,
    format_findings_markdown,
)
from agent.sandboxes.lifecycle import get_cached_sandbox_backend
from agent.sandboxes.paths import resolve_sandbox_work_dir

logger = logging.getLogger(__name__)

_MAX_CHANGED_FILES = 200
_SEVERITIES = ("low", "medium", "high", "critical")
_CONFIDENCES = ("low", "medium", "high")
_DISPOSITIONS = ("pending", "fixed", "deferred", "dismissed")

_COMPACT_FIELDS = (
    "id",
    "severity",
    "confidence",
    "category",
    "title",
    "description",
    "suggestion",
    "file",
    "start_line",
    "end_line",
    "disposition",
    "disposition_note",
)


def _thread_id() -> str:
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return thread_id if isinstance(thread_id, str) else ""


async def _current_review() -> InlineReview | None:
    """The most recently touched self-review claimed by this thread."""
    reviews = await REVIEWS.for_thread(_thread_id())
    if not reviews:
        return None
    return max(reviews, key=lambda review: review.updated_at)


def _compact(finding: InlineFinding) -> dict[str, Any]:
    dumped = finding.model_dump(mode="json")
    return {key: dumped[key] for key in _COMPACT_FIELDS if dumped.get(key) is not None}


def _no_review_result() -> dict[str, Any]:
    return {
        "success": False,
        "error": (
            "No pull request is claimed by this thread. Push the branch and call "
            "`open_pull_request` first."
        ),
    }


async def _resolve_repo_dir(sandbox_backend: Any, work_dir: str, repo: str) -> str | None:
    for candidate in (posixpath.join(work_dir, repo), work_dir):
        quoted = shlex.quote(candidate)
        result = await sandbox_backend.aexecute(f"git -C {quoted} rev-parse --show-toplevel")
        if result.exit_code == 0 and result.output.strip():
            return result.output.strip().splitlines()[-1].strip()
    return None


async def fetch_self_review_diff() -> dict[str, Any]:
    """Materialize this PR's diff in the sandbox and return bounded metadata.

    Call this first when self-reviewing the PR you just opened or updated. It
    writes the merge-base diff (what GitHub shows under "Files changed") to a
    file and returns its path plus the changed-file list — inspect the file with
    `grep` and paginated `read_file` calls rather than printing it.

    Returns:
        ``{success, path, bytes, files, file_count, base_sha, head_sha}``, or
        ``{success: False, error}``.
    """
    review = await _current_review()
    if review is None:
        return _no_review_result()

    sandbox_backend = get_cached_sandbox_backend(_thread_id())
    try:
        work_dir = await resolve_sandbox_work_dir(sandbox_backend)
        repo_dir = await _resolve_repo_dir(sandbox_backend, work_dir, review.repo)
        if repo_dir is None:
            return {"success": False, "error": "no git checkout found in the sandbox"}
        head = await sandbox_backend.aexecute(f"git -C {shlex.quote(repo_dir)} rev-parse HEAD")
        head_sha = head.output.strip() if head.exit_code == 0 else ""
        base_ref = review.base_sha or "origin/HEAD"
        materialized = await materialize_review_diff(
            sandbox_backend,
            work_dir=repo_dir,
            base_ref=base_ref,
            head_ref=head_sha or "HEAD",
            merge_base=True,
        )
    except (RuntimeError, ValueError) as exc:
        return {"success": False, "error": str(exc)}

    review.head_sha = head_sha or review.head_sha
    review.status = "reviewing"
    await REVIEWS.save(review)

    all_files = changed_files(materialized.diff_text)
    files = all_files[:_MAX_CHANGED_FILES]
    return {
        "success": True,
        "path": materialized.path,
        "bytes": len(materialized.diff_text.encode()),
        "files": files,
        "file_count": len(all_files),
        "files_truncated": len(all_files) > len(files),
        "base_sha": materialized.base_ref,
        "head_sha": materialized.head_ref,
    }


async def record_inline_finding(
    severity: str,
    confidence: str,
    category: str,
    file: str,
    title: str,
    description: str,
    start_line: int | None = None,
    end_line: int | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    """Record one self-review finding against the PR this thread authored.

    One call per distinct defect. The finding must name a concrete failure mode
    anchored to a line this PR changed — the same bar the PR reviewer applies.
    Findings are shown to the user in the thread's review panel; they are never
    posted to GitHub.

    Args:
        severity: One of ``low``, ``medium``, ``high``, ``critical``.
        confidence: One of ``low``, ``medium``, ``high``.
        category: Short label (``correctness``, ``security``, ``perf``, …).
        file: Repo-relative path.
        title: Concise headline naming the failure mode, roughly 4-10 words.
        description: Markdown body. Do not repeat the title as its first line.
        start_line: 1-based line in the post-PR file where the issue begins.
        end_line: 1-based line where it ends. Defaults to ``start_line``.
        suggestion: Replacement text for ``start_line..end_line``. Only for
            obvious fixes of 4 lines or fewer; longer ones are dropped.

    Returns:
        ``{success, finding_id, count}``, or ``{success: False, error}``.
    """
    if severity not in _SEVERITIES:
        return {"success": False, "error": f"severity must be one of {_SEVERITIES}"}
    if confidence not in _CONFIDENCES:
        return {"success": False, "error": f"confidence must be one of {_CONFIDENCES}"}
    if not file.strip():
        return {"success": False, "error": "file is required"}

    review = await _current_review()
    if review is None:
        return _no_review_result()
    if len(review.findings) >= MAX_INLINE_FINDINGS:
        return {
            "success": False,
            "error": f"the self-review cap of {MAX_INLINE_FINDINGS} findings is already reached",
        }

    clipped, dropped = clip_suggestion(suggestion)
    resolved_end = end_line if end_line is not None else start_line
    finding = InlineFinding(
        severity=cast(Severity, severity),
        confidence=cast(Confidence, confidence),
        category=category.strip(),
        title=normalize_finding_title(title, description),
        description=description,
        suggestion=clipped,
        file=file.strip(),
        start_line=start_line,
        end_line=resolved_end,
    )
    review.findings.append(finding)
    await REVIEWS.save(review)
    return {
        "success": True,
        "finding_id": finding.id,
        "count": len(review.findings),
        "suggestion_dropped": dropped,
    }


async def list_inline_findings() -> dict[str, Any]:
    """List the self-review findings recorded for this thread's pull request.

    Use this after the self-review pass to decide what to act on, and again
    before you report back to the user. Dashboard threads render the findings in
    their own panel; on Slack, Linear, and GitHub, paste the returned
    ``markdown`` block rather than reformatting the findings yourself.

    Returns:
        ``{success, pr_url, findings, count, markdown}``, or
        ``{success: False, error}``.
    """
    review = await _current_review()
    if review is None:
        return _no_review_result()
    findings = [_compact(finding) for finding in review.sorted_findings()]
    return {
        "success": True,
        "pr_url": review.pr_url,
        "findings": findings,
        "count": len(findings),
        "markdown": format_findings_markdown(review),
    }


async def set_inline_finding_disposition(
    finding_id: str,
    disposition: str,
    note: str,
) -> dict[str, Any]:
    """Record what you did about one self-review finding.

    Every finding needs a disposition before you finish the turn:

    - ``fixed`` — an obvious defect in code this PR introduced; you fixed it and
      pushed the fix. Say what you changed in ``note``.
    - ``deferred`` — the fix is ambiguous, or it would widen this PR's scope.
      Leave the code alone, state the question or the scope concern in ``note``,
      and ask the user about it.
    - ``dismissed`` — the finding is wrong on inspection. ``note`` says why.

    Args:
        finding_id: The id returned by ``record_inline_finding``.
        disposition: ``fixed``, ``deferred``, or ``dismissed``.
        note: One or two sentences the user will read next to the finding.

    Returns:
        ``{success, finding_id, disposition}``, or ``{success: False, error}``.
    """
    if disposition not in _DISPOSITIONS:
        return {"success": False, "error": f"disposition must be one of {_DISPOSITIONS}"}
    review = await _current_review()
    if review is None:
        return _no_review_result()
    finding = review.finding(finding_id)
    if finding is None:
        return {"success": False, "error": f"no finding with id {finding_id}"}
    finding.disposition = cast(Disposition, disposition)
    finding.disposition_note = note.strip()
    if all(item.disposition != "pending" for item in review.findings):
        review.status = "complete"
    await REVIEWS.save(review)
    return {"success": True, "finding_id": finding_id, "disposition": disposition}
