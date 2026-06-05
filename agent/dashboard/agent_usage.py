"""Forward-looking Open SWE Agent usage telemetry."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from langgraph_sdk import get_client

from ..reviewer_findings import REVIEWER_THREAD_KIND
from ..utils.github_app import get_github_app_installation_token

USAGE_THREAD_NAMESPACE: list[str] = ["agent_usage", "threads"]
USAGE_PR_NAMESPACE: list[str] = ["agent_usage", "prs"]
USAGE_LEADERBOARD_CACHE_NAMESPACE: list[str] = ["agent_usage", "leaderboard_cache"]
REVIEWER_STATS_CACHE_NAMESPACE: list[str] = ["agent_usage", "reviewer_stats_cache"]

Period = Literal["7d", "30d", "all"]
_AGENT_SOURCES = frozenset({"dashboard", "github", "slack", "linear"})
_PR_REFRESH_INTERVAL_MS = 10 * 60 * 1000
_MAX_PR_REFRESH_PER_REQUEST = 25
_PR_REFRESH_CONCURRENCY = 5
_CACHE_TTL_MS = 10 * 60 * 1000
_CACHE_SEARCH_LIMIT = 1000
_GITHUB_API = "https://api.github.com"

logger = logging.getLogger(__name__)


def _client():
    return get_client()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _period_cutoff_ms(period: str) -> int | None:
    now = datetime.now(UTC)
    if period == "7d":
        return int((now - timedelta(days=7)).timestamp() * 1000)
    if period == "30d":
        return int((now - timedelta(days=30)).timestamp() * 1000)
    return None


def _normalize_period(period: str | None) -> Period:
    return period if period in {"7d", "30d", "all"} else "30d"


def _record_from_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def _get_value(namespace: list[str], key: str) -> dict[str, Any] | None:
    try:
        item = await _client().store.get_item(namespace, key)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        raise
    return _record_from_item(item)


async def _search_values(
    namespace: list[str], *, limit: int = _CACHE_SEARCH_LIMIT
) -> list[dict[str, Any]]:
    result = await _client().store.search_items(namespace, limit=limit)
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    values: list[dict[str, Any]] = []
    for item in items or []:
        record = _record_from_item(item)
        if record:
            values.append(record)
    return values


def _user_key(github_login: str | None, email: str | None) -> str:
    login = github_login.strip().lower() if isinstance(github_login, str) else ""
    if login:
        return f"github:{login}"
    norm_email = email.strip().lower() if isinstance(email, str) else ""
    if norm_email:
        return f"email:{norm_email}"
    return "unknown"


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _timestamp_ms(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.isdigit():
            return _timestamp_ms(int(raw))
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp() * 1000)
    return 0


def _in_period(record: dict[str, Any], cutoff_ms: int | None) -> bool:
    if cutoff_ms is None:
        return True
    created_at = _coerce_int(record.get("created_at_ms"))
    return created_at >= cutoff_ms


def _display_name(github_login: str, email: str) -> str:
    if github_login:
        return github_login
    if email:
        return email.split("@", 1)[0]
    return "Unknown user"


def _ensure_user(
    users: dict[str, dict[str, Any]],
    *,
    github_login: str | None,
    email: str | None,
) -> dict[str, Any]:
    login = github_login.strip() if isinstance(github_login, str) else ""
    norm_email = email.strip().lower() if isinstance(email, str) else ""
    key = _user_key(login, norm_email)
    user = users.get(key)
    if user is None:
        user = {
            "key": key,
            "github_login": login,
            "email": norm_email,
            "name": _display_name(login, norm_email),
            "agent_runs": 0,
            "prs_opened": 0,
            "merged_prs": 0,
            "agent_loc": 0,
            "additions": 0,
            "deletions": 0,
            "model_counts": Counter(),
        }
        users[key] = user
    elif login and not user.get("github_login"):
        user["github_login"] = login
        user["name"] = _display_name(login, norm_email)
    if norm_email and not user.get("email"):
        user["email"] = norm_email
    return user


async def record_agent_thread_usage(
    *,
    thread_id: str,
    github_login: str | None,
    user_email: str | None,
    model_id: str,
    effort: str | None,
    source: str | None,
) -> None:
    """Record one Open SWE Agent thread for leaderboard aggregation."""
    if not thread_id:
        return
    source_value = source if isinstance(source, str) and source in _AGENT_SOURCES else "dashboard"
    now_ms = _now_ms()
    existing = await _get_value(USAGE_THREAD_NAMESPACE, thread_id)
    value = {
        **(existing or {}),
        "thread_id": thread_id,
        "github_login": github_login.strip() if isinstance(github_login, str) else "",
        "user_email": user_email.strip().lower() if isinstance(user_email, str) else "",
        "model_id": model_id,
        "effort": effort or "",
        "source": source_value,
        "agent_kind": "agent",
        "updated_at_ms": now_ms,
    }
    if not existing:
        value["created_at_ms"] = now_ms
    elif not value.get("created_at_ms"):
        value["created_at_ms"] = existing.get("created_at_ms") or now_ms
    await _client().store.put_item(USAGE_THREAD_NAMESPACE, thread_id, value)


async def record_agent_pr_usage(
    *,
    thread_id: str | None,
    github_login: str | None,
    user_email: str | None,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str | None,
    head: str,
    base: str,
    additions: int = 0,
    deletions: int = 0,
    changed_files: int = 0,
    state: str | None = None,
    merged: bool = False,
) -> None:
    """Record one Open SWE Agent pull request for leaderboard aggregation."""
    if not owner or not repo or not pr_number:
        return
    key = f"{owner}/{repo}#{pr_number}"
    now_ms = _now_ms()
    existing = await _get_value(USAGE_PR_NAMESPACE, key)
    value = {
        **(existing or {}),
        "key": key,
        "thread_id": thread_id or "",
        "github_login": github_login.strip() if isinstance(github_login, str) else "",
        "user_email": user_email.strip().lower() if isinstance(user_email, str) else "",
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr_url or "",
        "head": head,
        "base": base,
        "additions": max(0, additions),
        "deletions": max(0, deletions),
        "changed_files": max(0, changed_files),
        "state": state or "open",
        "merged": bool(merged),
        "agent_kind": "agent",
        "updated_at_ms": now_ms,
    }
    if not existing:
        value["created_at_ms"] = now_ms
    elif not value.get("created_at_ms"):
        value["created_at_ms"] = existing.get("created_at_ms") or now_ms
    await _client().store.put_item(USAGE_PR_NAMESPACE, key, value)


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _refresh_pr_record(
    client: httpx.AsyncClient, token: str, record: dict[str, Any]
) -> dict[str, Any]:
    owner = record.get("owner")
    repo = record.get("repo")
    pr_number = record.get("pr_number")
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(pr_number, int):
        return record
    resp = await client.get(
        f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}",
        headers=_github_headers(token),
    )
    if resp.status_code != 200:
        logger.debug(
            "GitHub returned %s refreshing usage PR %s/%s#%s",
            resp.status_code,
            owner,
            repo,
            pr_number,
        )
        return record
    data = resp.json()
    if not isinstance(data, dict):
        return record
    updated = {
        **record,
        "pr_url": data.get("html_url") or record.get("pr_url") or "",
        "state": data.get("state") if isinstance(data.get("state"), str) else record.get("state"),
        "merged": bool(data.get("merged")),
        "additions": data.get("additions") if isinstance(data.get("additions"), int) else 0,
        "deletions": data.get("deletions") if isinstance(data.get("deletions"), int) else 0,
        "changed_files": data.get("changed_files")
        if isinstance(data.get("changed_files"), int)
        else 0,
        "updated_at_ms": _now_ms(),
    }
    key = updated.get("key")
    if isinstance(key, str) and key:
        await _client().store.put_item(USAGE_PR_NAMESPACE, key, updated)
    return updated


async def _refresh_pr_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now_ms = _now_ms()
    stale_indexes = [
        index
        for index, record in enumerate(records)
        if not (updated_at := _coerce_int(record.get("updated_at_ms")))
        or now_ms - updated_at >= _PR_REFRESH_INTERVAL_MS
    ][:_MAX_PR_REFRESH_PER_REQUEST]
    if not stale_indexes:
        return records
    token = await get_github_app_installation_token()
    if not token:
        return records

    refreshed = list(records)
    semaphore = asyncio.Semaphore(_PR_REFRESH_CONCURRENCY)

    async def refresh_one(index: int, client: httpx.AsyncClient) -> tuple[int, dict[str, Any]]:
        async with semaphore:
            try:
                return index, await _refresh_pr_record(client, token, records[index])
            except Exception:
                logger.debug("Failed to refresh usage PR record", exc_info=True)
                return index, records[index]

    async with httpx.AsyncClient(timeout=10.0) as client:
        for index, record in await asyncio.gather(
            *(refresh_one(index, client) for index in stale_indexes)
        ):
            refreshed[index] = record
    return refreshed


def _serialize_usage_user(index: int, user: dict[str, Any]) -> dict[str, Any]:
    model_counts: Counter[str] = user.get("model_counts", Counter())
    favorite_model = model_counts.most_common(1)[0][0] if model_counts else "default"
    return {
        "rank": index,
        "key": user["key"],
        "name": user.get("name") or "Unknown user",
        "github_login": user.get("github_login") or None,
        "email": user.get("email") or None,
        "favorite_model": favorite_model,
        "agent_runs": user["agent_runs"],
        "prs_opened": user["prs_opened"],
        "merged_prs": user["merged_prs"],
        "agent_loc": user["agent_loc"],
        "additions": user["additions"],
        "deletions": user["deletions"],
    }


async def _build_usage_leaderboard_snapshot(period: Period) -> dict[str, Any]:
    cutoff_ms = _period_cutoff_ms(period)
    users: dict[str, dict[str, Any]] = {}

    for thread in await _search_values(USAGE_THREAD_NAMESPACE):
        if thread.get("agent_kind") != "agent" or thread.get("source") not in _AGENT_SOURCES:
            continue
        if not _in_period(thread, cutoff_ms):
            continue
        user = _ensure_user(
            users,
            github_login=thread.get("github_login"),
            email=thread.get("user_email"),
        )
        user["agent_runs"] += 1
        model_id = thread.get("model_id")
        if isinstance(model_id, str) and model_id:
            user["model_counts"][model_id] += 1

    pr_records = [
        pr
        for pr in await _search_values(USAGE_PR_NAMESPACE)
        if pr.get("agent_kind") == "agent" and _in_period(pr, cutoff_ms)
    ]
    for pr in await _refresh_pr_records(pr_records):
        user = _ensure_user(
            users,
            github_login=pr.get("github_login"),
            email=pr.get("user_email"),
        )
        additions = _coerce_int(pr.get("additions"))
        deletions = _coerce_int(pr.get("deletions"))
        user["prs_opened"] += 1
        if pr.get("merged"):
            user["merged_prs"] += 1
        user["additions"] += additions
        user["deletions"] += deletions
        user["agent_loc"] += additions + deletions

    sorted_users = sorted(
        users.values(),
        key=lambda item: (
            -item["agent_loc"],
            -item["prs_opened"],
            -item["agent_runs"],
            item.get("name") or "",
        ),
    )
    return {
        "period": period,
        "users": [_serialize_usage_user(index, user) for index, user in enumerate(sorted_users, 1)],
        "total_members": len(sorted_users),
    }


async def refresh_usage_leaderboard_cache(period: str | None = "30d") -> dict[str, Any]:
    normalized_period = _normalize_period(period)
    snapshot = await _build_usage_leaderboard_snapshot(normalized_period)
    await _client().store.put_item(
        USAGE_LEADERBOARD_CACHE_NAMESPACE,
        normalized_period,
        {"generated_at_ms": _now_ms(), "snapshot": snapshot},
    )
    return snapshot


def _is_finding_surfaced(finding: dict[str, Any]) -> bool:
    surface = finding.get("surface") if isinstance(finding.get("surface"), dict) else {}
    state = surface.get("state")
    if state in {"surfaced", "resolve_pending", "resolved"}:
        return True
    if isinstance(finding.get("github_review_id"), int):
        return True
    if isinstance(finding.get("github_review_comment_id"), int):
        return True
    comment_ids = finding.get("github_review_comment_ids")
    thread_ids = finding.get("github_review_thread_ids")
    return bool(comment_ids or thread_ids)


def _is_finding_resolved_by_us(finding: dict[str, Any]) -> bool:
    if finding.get("status") != "resolved" or not _is_finding_surfaced(finding):
        return False
    surface = finding.get("surface") if isinstance(finding.get("surface"), dict) else {}
    return bool(
        surface.get("state") == "resolved"
        or finding.get("github_thread_resolved")
        or finding.get("github_resolved_thread_ids")
        or finding.get("resolution_note")
    )


def _thread_created_at_ms(thread: dict[str, Any], metadata: dict[str, Any]) -> int:
    timestamp = _thread_explicit_created_at_ms(thread, metadata)
    if timestamp:
        return timestamp
    for source in (
        metadata.get("updated_at_ms"),
        metadata.get("updated_at"),
        thread.get("updated_at"),
        thread.get("updatedAt"),
    ):
        timestamp = _timestamp_ms(source)
        if timestamp:
            return timestamp
    return _now_ms()


def _thread_explicit_created_at_ms(thread: dict[str, Any], metadata: dict[str, Any]) -> int:
    for source in (
        metadata.get("created_at_ms"),
        metadata.get("created_at"),
        thread.get("created_at"),
        thread.get("createdAt"),
    ):
        timestamp = _timestamp_ms(source)
        if timestamp:
            return timestamp
    return 0


def _counter_rows(counter: Counter[str], *, limit: int = 5) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


async def _iter_reviewer_thread_pages(cutoff_ms: int | None):
    client = _client()
    offset = 0
    while True:
        page = await client.threads.search(
            metadata={"kind": REVIEWER_THREAD_KIND},
            limit=_CACHE_SEARCH_LIMIT,
            offset=offset,
            sort_by="created_at",
            sort_order="desc",
        )
        if not page:
            return
        yield page
        if len(page) < _CACHE_SEARCH_LIMIT:
            return
        if cutoff_ms is not None:
            last_thread = next(
                (thread for thread in reversed(page) if isinstance(thread, dict)), None
            )
            metadata = last_thread.get("metadata") if isinstance(last_thread, dict) else None
            if isinstance(metadata, dict):
                last_created_at_ms = _thread_explicit_created_at_ms(last_thread, metadata)
                if last_created_at_ms and last_created_at_ms < cutoff_ms:
                    return
        offset += len(page)


async def _build_reviewer_stats_snapshot(period: Period) -> dict[str, Any]:
    cutoff_ms = _period_cutoff_ms(period)

    reviewed_prs = 0
    prs_with_findings = 0
    findings_recorded = 0
    surfaced_findings = 0
    addressed_findings = 0
    dismissed_findings = 0
    human_replies = 0
    resolved_after_update = 0
    severity_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()

    async for page in _iter_reviewer_thread_pages(cutoff_ms):
        for thread in page:
            if not isinstance(thread, dict):
                continue
            metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
            if cutoff_ms is not None and _thread_created_at_ms(thread, metadata) < cutoff_ms:
                continue

            reviewed_prs += 1
            findings = metadata.get("findings")
            if not isinstance(findings, list):
                continue
            valid_findings = [finding for finding in findings if isinstance(finding, dict)]
            if valid_findings:
                prs_with_findings += 1
            for finding in valid_findings:
                findings_recorded += 1
                severity = finding.get("severity")
                if isinstance(severity, str) and severity:
                    severity_counts[severity] += 1
                category = finding.get("category")
                if isinstance(category, str) and category:
                    category_counts[category] += 1
                interactions = finding.get("interactions")
                if isinstance(interactions, list):
                    human_replies += sum(
                        1
                        for interaction in interactions
                        if isinstance(interaction, dict)
                        and interaction.get("kind") == "human_reply"
                    )
                elif finding.get("last_human_reply_at"):
                    human_replies += 1

                surfaced = _is_finding_surfaced(finding)
                if surfaced:
                    surfaced_findings += 1
                if _is_finding_resolved_by_us(finding):
                    addressed_findings += 1
                    first_seen_sha = finding.get("first_seen_sha")
                    head_sha = metadata.get("head_sha") or finding.get("last_confirmed_sha")
                    if (
                        isinstance(first_seen_sha, str)
                        and isinstance(head_sha, str)
                        and first_seen_sha != head_sha
                    ):
                        resolved_after_update += 1
                if finding.get("status") == "dismissed":
                    dismissed_findings += 1

    unresolved_surfaced_findings = max(
        0, surfaced_findings - addressed_findings - dismissed_findings
    )
    resolution_rate = addressed_findings / surfaced_findings if surfaced_findings else 0.0
    return {
        "period": period,
        "reviewed_prs": reviewed_prs,
        "prs_with_findings": prs_with_findings,
        "findings_recorded": findings_recorded,
        "surfaced_findings": surfaced_findings,
        "addressed_findings": addressed_findings,
        "resolved_after_update": resolved_after_update,
        "dismissed_findings": dismissed_findings,
        "unresolved_surfaced_findings": unresolved_surfaced_findings,
        "resolution_rate": resolution_rate,
        "human_replies": human_replies,
        "severity_counts": dict(severity_counts),
        "top_categories": _counter_rows(category_counts),
    }


async def refresh_reviewer_stats_cache(period: str | None = "30d") -> dict[str, Any]:
    normalized_period = _normalize_period(period)
    snapshot = await _build_reviewer_stats_snapshot(normalized_period)
    await _client().store.put_item(
        REVIEWER_STATS_CACHE_NAMESPACE,
        normalized_period,
        {"generated_at_ms": _now_ms(), "snapshot": snapshot},
    )
    return snapshot


async def _cached_snapshot(
    namespace: list[str],
    period: Period,
    refresh: Callable[[str | None], Awaitable[dict[str, Any]]],
    *,
    schedule_refresh: Callable[[Period], None] | None = None,
) -> tuple[dict[str, Any], int | None]:
    cached = await _get_value(namespace, period)
    if cached:
        snapshot = cached.get("snapshot")
        generated_at_ms = _coerce_int(cached.get("generated_at_ms"))
        if isinstance(snapshot, dict):
            if not generated_at_ms or _now_ms() - generated_at_ms <= _CACHE_TTL_MS:
                return snapshot, generated_at_ms or None
            if schedule_refresh is not None:
                schedule_refresh(period)
                return snapshot, generated_at_ms
    return await refresh(period), _now_ms()


def _usage_payload_from_snapshot(
    snapshot: dict[str, Any],
    *,
    limit: int,
    current_login: str | None,
    current_email: str | None,
    generated_at_ms: int | None,
) -> dict[str, Any]:
    safe_limit = min(max(limit, 1), 100)
    current_keys = {
        _user_key(current_login, current_email),
        _user_key(current_login, None),
        _user_key(None, current_email),
    }
    rows: list[dict[str, Any]] = []
    current_user_row: dict[str, Any] | None = None
    for user in snapshot.get("users", []):
        if not isinstance(user, dict):
            continue
        github_login = (
            user.get("github_login") if isinstance(user.get("github_login"), str) else None
        )
        is_current_user = user.get("key") in current_keys
        row = {
            "rank": _coerce_int(user.get("rank")),
            "user": {
                "name": user.get("name") if is_current_user or github_login else "Open SWE user",
                "github_login": github_login,
                "email": (user.get("email") or None) if is_current_user else None,
            },
            "favorite_model": user.get("favorite_model") or "default",
            "agent_runs": _coerce_int(user.get("agent_runs")),
            "prs_opened": _coerce_int(user.get("prs_opened")),
            "merged_prs": _coerce_int(user.get("merged_prs")),
            "agent_loc": _coerce_int(user.get("agent_loc")),
            "additions": _coerce_int(user.get("additions")),
            "deletions": _coerce_int(user.get("deletions")),
        }
        if is_current_user:
            current_user_row = row
        if len(rows) < safe_limit:
            rows.append(row)

    if current_user_row and all(row["rank"] != current_user_row["rank"] for row in rows):
        rows.append(current_user_row)

    return {
        "period": snapshot.get("period") or "30d",
        "rows": rows,
        "total_members": _coerce_int(snapshot.get("total_members")),
        "current_user_rank": current_user_row["rank"] if current_user_row else None,
        "generated_at_ms": generated_at_ms,
    }


async def list_agent_usage_leaderboard(
    *,
    period: str | None,
    limit: int,
    current_login: str | None,
    current_email: str | None,
    schedule_usage_refresh: Callable[[Period], None] | None = None,
    schedule_reviewer_refresh: Callable[[Period], None] | None = None,
) -> dict[str, Any]:
    """Return cached Open SWE Agent and reviewer usage stats."""
    normalized_period = _normalize_period(period)
    usage_snapshot, generated_at_ms = await _cached_snapshot(
        USAGE_LEADERBOARD_CACHE_NAMESPACE,
        normalized_period,
        refresh_usage_leaderboard_cache,
        schedule_refresh=schedule_usage_refresh,
    )
    reviewer_stats, reviewer_generated_at_ms = await _cached_snapshot(
        REVIEWER_STATS_CACHE_NAMESPACE,
        normalized_period,
        refresh_reviewer_stats_cache,
        schedule_refresh=schedule_reviewer_refresh,
    )
    payload = _usage_payload_from_snapshot(
        usage_snapshot,
        limit=limit,
        current_login=current_login,
        current_email=current_email,
        generated_at_ms=generated_at_ms,
    )
    payload["reviewer_stats"] = {**reviewer_stats, "generated_at_ms": reviewer_generated_at_ms}
    return payload
