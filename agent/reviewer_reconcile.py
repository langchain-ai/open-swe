from __future__ import annotations

from typing import Any

from .reviewer_findings import Finding, list_findings, replace_findings


def _human_replies_after_bot_comment(
    thread: dict[str, Any],
    *,
    bot_comment_id: int,
) -> list[dict[str, Any]]:
    comments = thread.get("comments")
    if not isinstance(comments, list):
        return []

    seen_bot_comment = False
    replies: list[dict[str, Any]] = []
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


async def reconcile_findings_with_review_threads(
    thread_id: str,
    threads: list[dict[str, Any]],
) -> list[Finding]:
    """Sync tracked Open SWE findings with the current GitHub review-thread state."""
    findings = await list_findings(thread_id)
    if not findings:
        return findings

    threads_by_thread_id = {
        thread.get("id"): thread
        for thread in threads
        if isinstance(thread.get("id"), str) and thread.get("id")
    }
    threads_by_comment_id: dict[int, dict[str, Any]] = {}
    for thread in threads:
        comments = thread.get("comments")
        if not isinstance(comments, list):
            continue
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_id = comment.get("id")
            if isinstance(comment_id, int):
                threads_by_comment_id[comment_id] = thread

    updated = False
    for finding in findings:
        github_thread_id = finding.get("github_review_thread_id")
        github_comment_id = finding.get("github_review_comment_id")
        thread = None
        if isinstance(github_thread_id, str) and github_thread_id:
            thread = threads_by_thread_id.get(github_thread_id)
        if thread is None and isinstance(github_comment_id, int):
            thread = threads_by_comment_id.get(github_comment_id)
        if thread is None:
            continue

        if not isinstance(github_thread_id, str) or not github_thread_id:
            new_thread_id = thread.get("id")
            if isinstance(new_thread_id, str) and new_thread_id:
                finding["github_review_thread_id"] = new_thread_id
                updated = True

        if thread.get("is_resolved") or thread.get("is_outdated"):
            if finding.get("status") == "open":
                finding["status"] = "resolved"
                finding["last_reconciliation_note"] = "GitHub thread is resolved or outdated."
                updated = True
            if thread.get("is_resolved") and not finding.get("github_thread_resolved"):
                finding["github_thread_resolved"] = True
                updated = True

        if isinstance(github_comment_id, int):
            replies = _human_replies_after_bot_comment(thread, bot_comment_id=github_comment_id)
            if replies:
                latest = replies[-1]
                body = latest.get("body") if isinstance(latest.get("body"), str) else ""
                if len(body) > 1000:
                    body = body[:1000] + "\n...[truncated]"
                created_at = (
                    latest.get("created_at") if isinstance(latest.get("created_at"), str) else ""
                )
                author = latest.get("author") if isinstance(latest.get("author"), str) else ""
                if finding.get("last_human_reply_at") != created_at:
                    finding["last_human_reply_at"] = created_at
                    finding["last_human_reply_author"] = author
                    finding["last_human_reply_body"] = body
                    finding["last_reconciliation_note"] = (
                        "Human replied to this review thread; reassess before taking action."
                    )
                    updated = True

    if updated:
        await replace_findings(thread_id, findings)
    return findings
