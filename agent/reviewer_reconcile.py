from __future__ import annotations

from typing import Any

from .reviewer_findings import Finding, list_findings, replace_findings

ReviewThread = dict[str, Any]


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
        if author == "open-swe[bot]":
            continue
        replies.append(comment)
    return replies


def _index_review_threads(
    review_threads: list[ReviewThread],
) -> tuple[dict[str, ReviewThread], dict[int, ReviewThread]]:
    by_thread_id = {
        thread_id: review_thread
        for review_thread in review_threads
        if isinstance(thread_id := review_thread.get("id"), str) and thread_id
    }
    by_comment_id: dict[int, ReviewThread] = {}
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
    return by_thread_id, by_comment_id


def _find_review_thread_for_finding(
    finding: Finding,
    *,
    by_thread_id: dict[str, ReviewThread],
    by_comment_id: dict[int, ReviewThread],
) -> ReviewThread | None:
    github_thread_id = finding.get("github_review_thread_id")
    if isinstance(github_thread_id, str) and github_thread_id:
        review_thread = by_thread_id.get(github_thread_id)
        if review_thread is not None:
            return review_thread

    github_comment_id = finding.get("github_review_comment_id")
    if isinstance(github_comment_id, int):
        return by_comment_id.get(github_comment_id)
    return None


def _sync_thread_status(finding: Finding, review_thread: ReviewThread) -> bool:
    updated = False
    github_thread_id = finding.get("github_review_thread_id")
    if not isinstance(github_thread_id, str) or not github_thread_id:
        new_thread_id = review_thread.get("id")
        if isinstance(new_thread_id, str) and new_thread_id:
            finding["github_review_thread_id"] = new_thread_id
            updated = True

    if review_thread.get("is_resolved") or review_thread.get("is_outdated"):
        if finding.get("status") == "open":
            finding["status"] = "resolved"
            finding["last_reconciliation_note"] = "GitHub thread is resolved or outdated."
            updated = True
        if review_thread.get("is_resolved") and not finding.get("github_thread_resolved"):
            finding["github_thread_resolved"] = True
            updated = True
    return updated


def _sync_latest_human_reply(finding: Finding, review_thread: ReviewThread) -> bool:
    github_comment_id = finding.get("github_review_comment_id")
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

    by_thread_id, by_comment_id = _index_review_threads(review_threads)

    updated = False
    for finding in findings:
        review_thread = _find_review_thread_for_finding(
            finding,
            by_thread_id=by_thread_id,
            by_comment_id=by_comment_id,
        )
        if review_thread is None:
            continue

        updated = _sync_thread_status(finding, review_thread) or updated
        updated = _sync_latest_human_reply(finding, review_thread) or updated

    if updated:
        await replace_findings(reviewer_thread_id, findings)
    return findings
