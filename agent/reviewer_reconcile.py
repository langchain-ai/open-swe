from __future__ import annotations

from typing import Any

from .reviewer_findings import Finding, list_findings, replace_findings
from .reviewer_publish import parse_review_comment_marker

ReviewThread = dict[str, Any]
ReviewThreadMatch = tuple[ReviewThread, int | None]

_OPEN_SWE_BOT_AUTHORS = {"open-swe", "open-swe[bot]"}


def _is_open_swe_bot_comment(comment: ReviewThread) -> bool:
    return comment.get("author") in _OPEN_SWE_BOT_AUTHORS


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int)]


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _human_replies_after_bot_comment(
    review_thread: ReviewThread,
    *,
    bot_comment_id: int,
) -> list[ReviewThread]:
    comments = review_thread.get("comments")
    if not isinstance(comments, list):
        return []

    seen_bot_comment = False
    replies: list[ReviewThread] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        comment_id = comment.get("id")
        if comment_id == bot_comment_id:
            seen_bot_comment = True
            continue
        if not seen_bot_comment:
            continue
        author = comment.get("author")
        if author in _OPEN_SWE_BOT_AUTHORS:
            continue
        replies.append(comment)
    return replies


def _index_review_threads(
    review_threads: list[ReviewThread],
) -> tuple[dict[str, ReviewThread], dict[int, ReviewThread], dict[str, list[ReviewThreadMatch]]]:
    by_thread_id = {
        thread_id: review_thread
        for review_thread in review_threads
        if isinstance(thread_id := review_thread.get("id"), str) and thread_id
    }
    by_comment_id: dict[int, ReviewThread] = {}
    by_marker_id: dict[str, list[ReviewThreadMatch]] = {}
    for review_thread in review_threads:
        comments = review_thread.get("comments")
        if not isinstance(comments, list):
            continue
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_id = comment.get("id")
            if isinstance(comment_id, int):
                by_comment_id[comment_id] = review_thread
            body = comment.get("body")
            if not isinstance(body, str) or not _is_open_swe_bot_comment(comment):
                continue
            marker = parse_review_comment_marker(body)
            if marker is not None and isinstance(comment_id, int):
                by_marker_id.setdefault(marker["id"], []).append((review_thread, comment_id))
    return by_thread_id, by_comment_id, by_marker_id


def _find_review_threads_for_finding(
    finding: Finding,
    *,
    by_thread_id: dict[str, ReviewThread],
    by_comment_id: dict[int, ReviewThread],
    by_marker_id: dict[str, list[ReviewThreadMatch]],
) -> list[ReviewThreadMatch]:
    finding_id = finding.get("id")
    if isinstance(finding_id, str):
        marker_match = by_marker_id.get(finding_id)
        if marker_match:
            return marker_match

    github_thread_id = finding.get("github_review_thread_id")
    if isinstance(github_thread_id, str) and github_thread_id:
        review_thread = by_thread_id.get(github_thread_id)
        if review_thread is not None:
            comment_id = finding.get("github_review_comment_id")
            return [(review_thread, comment_id if isinstance(comment_id, int) else None)]

    github_comment_id = finding.get("github_review_comment_id")
    if isinstance(github_comment_id, int):
        review_thread = by_comment_id.get(github_comment_id)
        if review_thread is not None:
            return [(review_thread, github_comment_id)]
    return []


def _sync_publication_identity(
    finding: Finding,
    review_thread: ReviewThread,
    comment_id: int | None,
) -> bool:
    updated = False
    if isinstance(comment_id, int) and not isinstance(finding.get("github_review_comment_id"), int):
        finding["github_review_comment_id"] = comment_id
        updated = True
    comment_ids = _int_list(finding.get("github_review_comment_ids"))
    if isinstance(comment_id, int) and comment_id not in comment_ids:
        comment_ids.append(comment_id)
        finding["github_review_comment_ids"] = comment_ids
        updated = True

    github_thread_id = finding.get("github_review_thread_id")
    new_thread_id = review_thread.get("id")
    if not isinstance(github_thread_id, str) or not github_thread_id:
        if isinstance(new_thread_id, str) and new_thread_id:
            finding["github_review_thread_id"] = new_thread_id
            updated = True
    thread_ids = _str_list(finding.get("github_review_thread_ids"))
    if isinstance(new_thread_id, str) and new_thread_id and new_thread_id not in thread_ids:
        thread_ids.append(new_thread_id)
        finding["github_review_thread_ids"] = thread_ids
        updated = True
    return updated


def _is_terminal_thread(review_thread: ReviewThread) -> bool:
    return bool(review_thread.get("is_resolved") or review_thread.get("is_outdated"))


def _sync_thread_status(finding: Finding, matches: list[ReviewThreadMatch]) -> bool:
    if not matches or not all(_is_terminal_thread(review_thread) for review_thread, _ in matches):
        return False

    updated = False
    if finding.get("status") == "open":
        finding["status"] = "resolved"
        finding["last_reconciliation_note"] = "All GitHub threads are resolved or outdated."
        updated = True

    resolved_thread_ids = _str_list(finding.get("github_resolved_thread_ids"))
    all_resolved = True
    for review_thread, _comment_id in matches:
        thread_id = review_thread.get("id")
        if review_thread.get("is_resolved") and isinstance(thread_id, str) and thread_id:
            if thread_id not in resolved_thread_ids:
                resolved_thread_ids.append(thread_id)
                updated = True
        else:
            all_resolved = False

    if resolved_thread_ids != _str_list(finding.get("github_resolved_thread_ids")):
        finding["github_resolved_thread_ids"] = resolved_thread_ids
    if all_resolved and not finding.get("github_thread_resolved"):
        finding["github_thread_resolved"] = True
        updated = True
    return updated


def _sync_latest_human_reply(
    finding: Finding,
    review_thread: ReviewThread,
    *,
    comment_id: int | None,
) -> bool:
    github_comment_id = (
        comment_id if isinstance(comment_id, int) else finding.get("github_review_comment_id")
    )
    if not isinstance(github_comment_id, int):
        return False

    replies = _human_replies_after_bot_comment(review_thread, bot_comment_id=github_comment_id)
    if not replies:
        return False

    latest = replies[-1]
    body = latest.get("body") if isinstance(latest.get("body"), str) else ""
    if len(body) > 1000:
        body = body[:1000] + "\n...[truncated]"
    created_at = latest.get("created_at") if isinstance(latest.get("created_at"), str) else ""
    if finding.get("last_human_reply_at") == created_at:
        return False

    author = latest.get("author") if isinstance(latest.get("author"), str) else ""
    finding["last_human_reply_at"] = created_at
    finding["last_human_reply_author"] = author
    finding["last_human_reply_body"] = body
    finding["last_reconciliation_note"] = (
        "Human replied to this review thread; reassess before taking action."
    )
    return True


async def reconcile_findings_with_review_threads(
    reviewer_thread_id: str,
    review_threads: list[ReviewThread],
) -> list[Finding]:
    """Sync tracked Open SWE findings with the current GitHub review-thread state."""
    findings = await list_findings(reviewer_thread_id)
    if not findings:
        return findings

    by_thread_id, by_comment_id, by_marker_id = _index_review_threads(review_threads)

    updated = False
    for finding in findings:
        matches = _find_review_threads_for_finding(
            finding,
            by_thread_id=by_thread_id,
            by_comment_id=by_comment_id,
            by_marker_id=by_marker_id,
        )
        for review_thread, comment_id in matches:
            updated = _sync_publication_identity(finding, review_thread, comment_id) or updated
            updated = (
                _sync_latest_human_reply(finding, review_thread, comment_id=comment_id) or updated
            )
        updated = _sync_thread_status(finding, matches) or updated

    if updated:
        await replace_findings(reviewer_thread_id, findings)
    return findings
