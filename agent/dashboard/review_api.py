"""Read API for the PR review UI.

Reviewer threads (``metadata.kind == "reviewer"``) hold the durable review
state for a PR: identity (``pr``), findings, watch flag, and head SHA. These
endpoints surface that state plus live PR details/diff fetched from GitHub
with the App installation token.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx
from fastapi import HTTPException

from ..reviewer_diff import parse_unified_diff
from ..reviewer_findings import REVIEWER_THREAD_KIND
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_checks import github_headers
from ..utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

DiffLineKind = Literal["context", "add", "del"]

_DIFF_NEW_FILE_RE = re.compile(r"^new file mode", re.MULTILINE)
_DIFF_DELETED_FILE_RE = re.compile(r"^deleted file mode", re.MULTILINE)
_DIFF_RENAME_RE = re.compile(r"^rename from ", re.MULTILINE)


async def _require_app_token() -> str:
    token = await get_github_app_installation_token()
    if not token:
        raise HTTPException(503, "GitHub App token unavailable")
    return token


async def _github_get(
    path: str, token: str, *, accept: str | None = None, params: dict[str, Any] | None = None
) -> Any:
    headers = github_headers(token)
    if accept:
        headers["Accept"] = accept
    async with httpx.AsyncClient(timeout=_GITHUB_TIMEOUT) as client:
        response = await client.get(f"{_GITHUB_API}{path}", headers=headers, params=params)
    if response.status_code == 404:
        raise HTTPException(404, "not found on GitHub")
    if response.status_code >= 400:
        logger.warning("GitHub GET %s failed: %s", path, response.status_code)
        raise HTTPException(502, f"GitHub request failed ({response.status_code})")
    if accept and "json" not in accept:
        return response.text
    return response.json()


def reviewer_thread_id(owner: str, repo: str, pr_number: int) -> str:
    import uuid

    stable_key = f"{owner}/{repo}/pr/{pr_number}/reviewer"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))


def _findings_list(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    findings = metadata.get("findings")
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, dict) and isinstance(f.get("id"), str)]


def _serialize_finding(finding: dict[str, Any], head_sha: str | None) -> dict[str, Any]:
    last_confirmed = finding.get("last_confirmed_sha")
    outdated = bool(
        head_sha
        and isinstance(last_confirmed, str)
        and last_confirmed
        and last_confirmed != head_sha
    )
    interactions = finding.get("interactions")
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
        "github_thread_resolved": bool(finding.get("github_thread_resolved")),
        "github_review_comment_id": (
            finding["github_review_comment_id"]
            if isinstance(finding.get("github_review_comment_id"), int)
            else None
        ),
        "interactions": interactions if isinstance(interactions, list) else [],
    }


_BUG_SEVERITIES = frozenset({"high", "critical"})


def classify_finding(finding: dict[str, Any]) -> Literal["bug", "investigate", "informational"]:
    """Map our severity/confidence model onto the UI's Bugs/Flags split."""
    severity = finding.get("severity", "low")
    confidence = finding.get("confidence", "medium")
    if severity in _BUG_SEVERITIES and confidence == "high":
        return "bug"
    if severity != "low":
        return "investigate"
    return "informational"


def _finding_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
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


def _run_status(thread: dict[str, Any], metadata: dict[str, Any]) -> str:
    if thread.get("status") == "busy":
        return "running"
    latest = metadata.get("latest_run_status")
    if latest in {"pending", "running"}:
        return "running"
    if latest in {"error", "failed", "timeout", "interrupted"}:
        return "error"
    return "idle"


def _thread_review_summary(thread: dict[str, Any]) -> dict[str, Any] | None:
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    pr = metadata.get("pr")
    if not isinstance(pr, dict):
        return None
    owner = pr.get("owner")
    name = pr.get("name")
    number = pr.get("number")
    if not (isinstance(owner, str) and isinstance(name, str) and isinstance(number, int)):
        return None
    findings = _findings_list(metadata)
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
        "head_sha": metadata.get("head_sha") or "",
        "watch": bool(metadata.get("watch")),
        "status": _run_status(thread, metadata),
        "counts": _finding_counts(findings),
        "updated_at": updated_at if isinstance(updated_at, str) else None,
    }


async def list_reviews(
    limit: int = 100,
    *,
    is_accessible: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
    page_size: int = 100,
    max_scan: int = 1000,
) -> list[dict[str, Any]]:
    """List review summaries, newest first.

    When ``is_accessible`` is given, keeps paging through reviewer threads
    until ``limit`` accessible summaries are collected (or ``max_scan``
    threads have been examined), so inaccessible records don't crowd
    accessible ones out of a single fixed-size page.
    """
    client = langgraph_client()
    summaries: list[dict[str, Any]] = []
    offset = 0
    while len(summaries) < limit and offset < max_scan:
        threads = await client.threads.search(
            metadata={"kind": REVIEWER_THREAD_KIND},
            limit=page_size,
            offset=offset,
            sort_by="updated_at",
            sort_order="desc",
        )
        if not threads:
            break
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            summary = _thread_review_summary(thread)
            if not summary:
                continue
            if is_accessible is not None and not await is_accessible(summary):
                continue
            summaries.append(summary)
            if len(summaries) >= limit:
                break
        if len(threads) < page_size:
            break
        offset += page_size
    return summaries


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


async def _fetch_check_runs(owner: str, repo: str, sha: str, token: str) -> list[dict[str, Any]]:
    if not sha:
        return []
    try:
        payload = await _github_get(
            f"/repos/{owner}/{repo}/commits/{sha}/check-runs",
            token,
            params={"per_page": 50},
        )
    except HTTPException:
        return []
    runs = payload.get("check_runs") if isinstance(payload, dict) else None
    out: list[dict[str, Any]] = []
    for run in runs if isinstance(runs, list) else []:
        if not isinstance(run, dict):
            continue
        out.append(
            {
                "name": run.get("name") or "",
                "status": run.get("status") or "",
                "conclusion": run.get("conclusion"),
                "url": run.get("html_url"),
            }
        )
    return out


async def get_review(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    thread_id = reviewer_thread_id(owner, repo, pr_number)
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "review not found") from exc
    if not isinstance(thread, dict):
        raise HTTPException(404, "review not found")
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    summary = _thread_review_summary(thread)
    if not summary:
        raise HTTPException(404, "review not found")

    token = await _require_app_token()
    pr_payload = await _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token)
    details = _serialize_pr_details(pr_payload if isinstance(pr_payload, dict) else {})
    head_sha = details["head_sha"] or summary["head_sha"]
    checks = await _fetch_check_runs(owner, repo, head_sha, token)

    findings = [_serialize_finding(finding, head_sha) for finding in _findings_list(metadata)]
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

    return {**summary, "pr": details, "checks": checks, "findings": findings}


def parse_diff_files(diff_text: str) -> list[dict[str, Any]]:
    """Parse a unified diff into renderable per-file hunk/line records.

    Iterates the raw ``diff --git`` sections so metadata-only changes
    (pure renames, mode changes) still appear, with empty hunks.
    """
    raw_sections = _split_file_sections(diff_text)
    parsed_by_file = {file_diff.file: file_diff for file_diff in parse_unified_diff(diff_text)}
    files: list[dict[str, Any]] = []
    for path, section in raw_sections.items():
        status = "modified"
        if _DIFF_NEW_FILE_RE.search(section):
            status = "added"
        elif _DIFF_DELETED_FILE_RE.search(section):
            status = "deleted"
        elif _DIFF_RENAME_RE.search(section):
            status = "renamed"
        file_diff = parsed_by_file.get(path)
        hunks = []
        additions = 0
        deletions = 0
        for hunk in file_diff.hunks if file_diff else ():
            lines: list[dict[str, Any]] = []
            old_line = hunk.old_start
            new_line = hunk.new_start
            body_lines = hunk.body.splitlines()
            for raw in body_lines[1:]:
                if raw.startswith("+"):
                    lines.append({"kind": "add", "new_line": new_line, "text": raw[1:]})
                    new_line += 1
                    additions += 1
                elif raw.startswith("-"):
                    lines.append({"kind": "del", "old_line": old_line, "text": raw[1:]})
                    old_line += 1
                    deletions += 1
                elif raw.startswith("\\"):
                    continue
                else:
                    lines.append(
                        {
                            "kind": "context",
                            "old_line": old_line,
                            "new_line": new_line,
                            "text": raw[1:] if raw.startswith(" ") else raw,
                        }
                    )
                    old_line += 1
                    new_line += 1
            hunks.append(
                {
                    "header": body_lines[0] if body_lines else "",
                    "old_start": hunk.old_start,
                    "new_start": hunk.new_start,
                    "lines": lines,
                }
            )
        files.append(
            {
                "path": path,
                "status": status,
                "additions": additions,
                "deletions": deletions,
                "hunks": hunks,
            }
        )
    return files


def _split_file_sections(diff_text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_file: str | None = None
    current_lines: list[str] = []
    header_re = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)$")
    for line in diff_text.splitlines():
        match = header_re.match(line)
        if match:
            if current_file is not None:
                sections[current_file] = "\n".join(current_lines)
            current_file = match.group("b")
            current_lines = [line]
        elif current_file is not None:
            current_lines.append(line)
    if current_file is not None:
        sections[current_file] = "\n".join(current_lines)
    return sections


async def get_review_diff(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    token = await _require_app_token()
    diff_text = await _github_get(
        f"/repos/{owner}/{repo}/pulls/{pr_number}",
        token,
        accept="application/vnd.github.diff",
    )
    files = parse_diff_files(diff_text if isinstance(diff_text, str) else "")
    return {
        "files": files,
        "total_additions": sum(f["additions"] for f in files),
        "total_deletions": sum(f["deletions"] for f in files),
    }


async def trigger_re_review(owner: str, repo: str, pr_number: int, login: str) -> dict[str, Any]:
    from ..utils.slack import GitHubPrRef
    from ..webapp import trigger_pr_review_from_ref

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
