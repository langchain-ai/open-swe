"""Read API for the PR review UI.

Reviewer threads (``metadata.kind == "reviewer"``) hold the durable review
state for a PR: identity (``pr``), findings, watch flag, and head SHA. These
endpoints surface that state plus live PR details/diff fetched from GitHub
with the App installation token.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException, Response

from ..reviewer_findings import REVIEWER_THREAD_KIND
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_checks import github_headers
from ..utils.thread_ops import langgraph_client
from .pr_diff import build_pr_diff_files

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_GITHUB_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


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


def _github_error_message(response: httpx.Response) -> str:
    """Best-effort extraction of GitHub's error message for surfacing to the UI."""
    fallback = f"GitHub request failed ({response.status_code})"
    try:
        data = response.json()
    except ValueError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    message = data.get("message")
    message_str = message if isinstance(message, str) else ""
    errors = data.get("errors")
    detail_parts: list[str] = []
    if isinstance(errors, list):
        for err in errors:
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                detail_parts.append(err["message"])
    detail = "; ".join(detail_parts)
    if message_str and detail:
        return f"{message_str}: {detail}"
    return message_str or detail or fallback


async def _github_post(path: str, token: str, *, json: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=_GITHUB_TIMEOUT) as client:
        response = await client.post(
            f"{_GITHUB_API}{path}", headers=github_headers(token), json=json
        )
    if response.status_code >= 400:
        message = _github_error_message(response)
        logger.warning("GitHub POST %s failed: %s %s", path, response.status_code, message)
        # Pass 4xx through verbatim (422 = line not in diff, 403 = perms); collapse
        # 5xx to a 502 so a GitHub outage doesn't masquerade as a client error.
        raise HTTPException(response.status_code if response.status_code < 500 else 502, message)
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


def _serialize_diff_groups(
    metadata: dict[str, Any], head_sha: str
) -> tuple[list[dict[str, Any]], bool]:
    """Serialize the persisted ``diff_groups`` for the AI sorted view.

    Returns ``(groups, stale)`` where each group carries a 1-based ``index``
    and validated fields, and ``stale`` is True when the groups were generated
    for a different head SHA than the one currently being rendered.
    """
    raw = metadata.get("diff_groups")
    if not isinstance(raw, dict):
        return [], False
    raw_groups = raw.get("groups")
    if not isinstance(raw_groups, list):
        return [], False
    groups: list[dict[str, Any]] = []
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        title = group.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        files = [f for f in (group.get("files") or []) if isinstance(f, str) and f]
        if not files:
            continue
        summary = group.get("summary")
        groups.append(
            {
                "index": len(groups) + 1,
                "title": title.strip(),
                "summary": summary.strip() if isinstance(summary, str) else "",
                "files": files,
            }
        )
    groups_head = raw.get("head_sha")
    stale = bool(
        head_sha and isinstance(groups_head, str) and groups_head and groups_head != head_sha
    )
    return groups, stale


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
        "author": pr.get("author") if isinstance(pr.get("author"), str) else "",
        "head_sha": metadata.get("head_sha") or "",
        "watch": bool(metadata.get("watch")),
        "status": _run_status(thread, metadata),
        "counts": _finding_counts(findings),
        "updated_at": updated_at if isinstance(updated_at, str) else None,
    }


async def list_reviews(
    limit: int = 20,
    *,
    offset: int = 0,
    author: str | None = None,
    is_accessible: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
    page_size: int = 100,
    max_scan: int = 1000,
) -> tuple[list[dict[str, Any]], bool]:
    """List review summaries, newest first.

    Returns ``(summaries, has_more)`` where the summaries are the page at
    ``offset`` (counted in accessible, filter-matching records) and
    ``has_more`` says whether at least one more record exists past it.

    ``author`` is pushed into the ``threads.search`` metadata filter
    (``pr.author`` containment), so the "My PRs" tab only fetches the user's
    own reviewer threads instead of scanning the whole population in Python.

    When ``is_accessible`` is given, keeps paging through reviewer threads
    until enough accessible summaries are collected (or ``max_scan`` threads
    have been examined), so inaccessible records don't crowd accessible ones
    out of a single fixed-size page.
    """
    client = langgraph_client()
    search_metadata: dict[str, Any] = {"kind": REVIEWER_THREAD_KIND}
    if author is not None:
        search_metadata["pr"] = {"author": author}
    needed = offset + limit + 1
    summaries: list[dict[str, Any]] = []
    scan_offset = 0
    while len(summaries) < needed and scan_offset < max_scan:
        threads = await client.threads.search(
            metadata=search_metadata,
            limit=page_size,
            offset=scan_offset,
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
            if len(summaries) >= needed:
                break
        if len(threads) < page_size:
            break
        scan_offset += page_size
    page = summaries[offset : offset + limit]
    return page, len(summaries) > offset + limit


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


async def get_pr_head_sha(owner: str, repo: str, pr_number: int) -> str:
    """Return the PR's current head SHA from GitHub, or "" if unavailable.

    A lightweight alternative to :func:`get_review` for callers that only need to
    detect whether the PR head has moved (e.g. the chat staleness check).
    """
    try:
        token = await _require_app_token()
        payload = await _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token)
    except HTTPException:
        return ""
    head = payload.get("head") if isinstance(payload, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    return sha if isinstance(sha, str) else ""


async def create_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    token: str,
    path: str,
    line: int,
    side: Literal["LEFT", "RIGHT"],
    body: str,
    start_line: int | None = None,
    start_side: Literal["LEFT", "RIGHT"] | None = None,
) -> dict[str, Any]:
    """Post a single inline review comment to a PR using the caller's token.

    Unlike the reviewer agent (which batches comments into one review via the App
    token), this posts a standalone comment immediately, authored by the signed-in
    user. ``commit_id`` is the PR's live head SHA. GitHub errors surface verbatim so
    the UI can explain a 422 (line not part of the diff) or 403 (missing permission).
    """
    head_sha = await get_pr_head_sha(owner, repo, pr_number)
    if not head_sha:
        raise HTTPException(502, "could not resolve PR head commit")
    payload: dict[str, Any] = {
        "body": body,
        "commit_id": head_sha,
        "path": path,
        "line": line,
        "side": side,
    }
    # GitHub forbids multi-line ranges that span sides; only add the range start
    # when it is a distinct earlier line on the same side.
    if start_line is not None and start_line != line:
        payload["start_line"] = start_line
        payload["start_side"] = start_side or side
    return await _github_post(
        f"/repos/{owner}/{repo}/pulls/{pr_number}/comments", token, json=payload
    )


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Inline comments the reviewer posts carry this hidden marker (see reviewer_publish).
_OPEN_SWE_COMMENT_RE = re.compile(r"<!--\s*open-swe-review-comment\b")
_REVIEW_COMMENTS_PER_PAGE = 100
# Bound the fetch so a pathological PR can't trigger unbounded paging (~2000 comments).
_MAX_REVIEW_COMMENT_PAGES = 20


def _clean_comment_body(body: str) -> str:
    return _HTML_COMMENT_RE.sub("", body).strip()


def _normalize_review_comment(item: dict[str, Any]) -> dict[str, Any]:
    body = item.get("body") if isinstance(item.get("body"), str) else ""
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    line = item.get("line")
    if not isinstance(line, int):
        original = item.get("original_line")
        line = original if isinstance(original, int) else None
    return {
        "id": item.get("id"),
        "author": user.get("login") if isinstance(user.get("login"), str) else "",
        "author_avatar_url": (
            user.get("avatar_url") if isinstance(user.get("avatar_url"), str) else ""
        ),
        "path": item.get("path") if isinstance(item.get("path"), str) else "",
        "line": line,
        "side": item.get("side") if item.get("side") in ("LEFT", "RIGHT") else "RIGHT",
        "body": _clean_comment_body(body),
        "html_url": item.get("html_url") if isinstance(item.get("html_url"), str) else "",
        "created_at": item.get("created_at") if isinstance(item.get("created_at"), str) else "",
        "is_open_swe": bool(_OPEN_SWE_COMMENT_RE.search(body)),
        # GitHub nulls `position` when the line no longer appears in the current
        # diff — i.e. the comment is outdated and can't be rendered inline.
        "is_outdated": not isinstance(item.get("position"), int),
    }


async def list_review_comments(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    """List inline review comments on a PR (newest first), normalized for the UI.

    Surfaces every inline comment on the PR — including humans' — not just the
    reviewer's findings. ``is_open_swe`` flags the reviewer's own (marker-bearing)
    comments so the UI can separate them from other people's. Pages through the
    full list (bounded by ``_MAX_REVIEW_COMMENT_PAGES``) so older comments aren't
    silently dropped.
    """
    token = await _require_app_token()
    comments: list[dict[str, Any]] = []
    for page in range(1, _MAX_REVIEW_COMMENT_PAGES + 1):
        raw = await _github_get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            token,
            params={
                "per_page": _REVIEW_COMMENTS_PER_PAGE,
                "page": page,
                "sort": "created",
                "direction": "desc",
            },
        )
        if not isinstance(raw, list) or not raw:
            break
        comments.extend(_normalize_review_comment(item) for item in raw if isinstance(item, dict))
        if len(raw) < _REVIEW_COMMENTS_PER_PAGE:
            break
    return {"comments": comments}


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

    diff_groups, diff_groups_stale = _serialize_diff_groups(metadata, head_sha)

    return {
        **summary,
        "pr": details,
        "checks": checks,
        "findings": findings,
        "diff_groups": diff_groups,
        "diff_groups_stale": diff_groups_stale,
    }


async def get_review_diff(owner: str, repo: str, pr_number: int) -> dict[str, Any]:
    """Return the PR's changed files with full original/modified contents.

    Uses the App installation token so the diff is available regardless of who
    is viewing the review. The client renders these with pierre's MultiFileDiff.
    """
    token = await _require_app_token()
    async with httpx.AsyncClient(headers=github_headers(token), timeout=_GITHUB_TIMEOUT) as client:
        diff = await build_pr_diff_files(client, f"{owner}/{repo}", pr_number)
    files = diff["files"]
    return {
        "files": files,
        "total_additions": sum(f["additions"] for f in files),
        "total_deletions": sum(f["deletions"] for f in files),
        "truncated": diff["truncated"],
    }


# --- PR description image proxy ----------------------------------------------
# PR bodies can embed images hosted on GitHub (user-attachment uploads,
# *.githubusercontent.com). For private repos those URLs require GitHub auth the
# browser doesn't have, so they render broken. We proxy them through the App
# installation token. The host allowlist + per-redirect public-IP check guard
# against SSRF (only GitHub-owned hosts are ever contacted).

_ALLOWED_IMAGE_HOST_SUFFIXES = (".githubusercontent.com",)
_MAX_IMAGE_REDIRECTS = 5
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
# Only safe raster formats — SVG (image/svg+xml) can execute script in our
# origin, so it is never served.
_ALLOWED_IMAGE_CONTENT_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}
)


def _is_allowed_image_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host == "github.com" or host == "www.github.com":
        # On github.com only user-attachment assets are images worth proxying.
        return parsed.path.startswith("/user-attachments/")
    return any(host.endswith(suffix) for suffix in _ALLOWED_IMAGE_HOST_SUFFIXES)


def _host_resolves_public(hostname: str) -> bool:
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not addr_infos:
        return False
    for addr_info in addr_infos:
        try:
            ip = ipaddress.ip_address(addr_info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def _validate_image_url(url: str) -> None:
    if not _is_allowed_image_url(url):
        raise HTTPException(400, "image host not allowed")
    hostname = urlparse(url).hostname or ""
    if not _host_resolves_public(hostname):
        raise HTTPException(400, "image host not allowed")


async def _require_image_in_pr(owner: str, repo: str, pr_number: int, url: str, token: str) -> None:
    """Bind the requested image to the authorized PR.

    The proxy fetches with the App installation token, which can read every repo
    the App is installed on. Without this check a caller authorized for one repo
    could proxy an image URL from another private repo (IDOR). Only URLs that
    actually appear in this PR's body are allowed.
    """
    pr_payload = await _github_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", token)
    body = pr_payload.get("body") or ""
    if url not in body:
        raise HTTPException(403, "image not referenced by this PR")


async def proxy_pr_image(owner: str, repo: str, pr_number: int, url: str) -> Response:
    """Stream a GitHub-hosted PR image through the App token.

    The URL must appear in the target PR's body (bound to the authorized
    resource), and every URL (including redirect targets) is validated against
    the GitHub host allowlist and a public-IP check before it is contacted.
    """
    _validate_image_url(url)
    token = await _require_app_token()
    await _require_image_in_pr(owner, repo, pr_number, url, token)
    headers = {"Authorization": f"Bearer {token}", "Accept": "image/*"}

    current_url = url
    async with httpx.AsyncClient(timeout=_GITHUB_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_IMAGE_REDIRECTS + 1):
            async with client.stream("GET", current_url, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get("Location")
                    if not location:
                        raise HTTPException(502, "image fetch failed (redirect without target)")
                    current_url = urljoin(str(response.url), location)
                    _validate_image_url(current_url)
                    continue

                if response.status_code >= 400:
                    raise HTTPException(502, f"image fetch failed ({response.status_code})")

                content_type = (
                    response.headers.get("Content-Type", "").lower().split(";", 1)[0].strip()
                )
                if content_type not in _ALLOWED_IMAGE_CONTENT_TYPES:
                    raise HTTPException(415, "unsupported image type")

                # Stream and abort once over the cap so a large (or lying) upstream
                # can't make the worker buffer the whole file.
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > _MAX_IMAGE_BYTES:
                        raise HTTPException(413, "image too large")

                return Response(
                    content=bytes(content),
                    media_type=content_type,
                    headers={
                        "Cache-Control": "private, max-age=300",
                        "X-Content-Type-Options": "nosniff",
                        "Content-Security-Policy": "default-src 'none'; sandbox",
                    },
                )

    raise HTTPException(502, "too many redirects fetching image")


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
