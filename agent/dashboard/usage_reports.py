"""The dashboard's usage read model: the agent leaderboard plus reviewer stats.

An aggregation over the source events :mod:`agent.settings.agent_usage` records
— agent runs, agent PRs, review publications, finding outcomes — read in full
on every rebuild and cached briefly in process. Records written before the
event model existed are migrated into it exactly once, on the first read.

This lives in the dashboard because it is the only caller and because the
backfill reads reviewer threads by :data:`agent.review.findings.REVIEWER_THREAD_KIND`,
which the settings foundation must not import.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from langgraph_sdk import get_client

from ..review.findings import REVIEWER_THREAD_KIND
from ..settings.agent_usage import (
    AGENT_PR_NAMESPACE,
    AGENT_RUN_NAMESPACE,
    AGENT_SOURCES,
    REVIEW_FINDING_NAMESPACE,
    REVIEW_NAMESPACE,
    finding_surfaced,
    human_reply_count,
    normalize_email,
    normalize_login,
    store_key,
    timestamp_ms,
    write_lock,
)
from ..store import get_value, now_ms, put_value, search_all_values
from ..utils.json_types import as_json_object, thread_metadata

LEGACY_THREAD_NAMESPACE: list[str] = ["agent_usage", "threads"]
LEGACY_PR_NAMESPACE: list[str] = ["agent_usage", "prs"]
BACKFILL_NAMESPACE: list[str] = ["usage", "v2", "backfill"]
_BACKFILL_KEY = "legacy_v1"

Period = Literal["7d", "30d", "all"]
_PAGE_SIZE = 1000
_MAX_ROWS = 100
# (period, viewer) -> (built_at_ms, payload, the viewer's own row)
_USAGE_CACHE: dict[tuple[str, str], tuple[int, dict[str, Any], dict[str, Any] | None]] = {}
_USAGE_CACHE_TTL_MS = 60_000

logger = logging.getLogger(__name__)


def _client():
    return get_client()


def _normalize_period(period: str | None) -> Period:
    if period == "7d" or period == "all":
        return period
    return "30d"


def _period_cutoff_ms(period: Period) -> int:
    days = 7 if period == "7d" else 30 if period == "30d" else 0
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000) if days else 0


async def _all(namespace: list[str]) -> list[dict[str, Any]]:
    return await search_all_values(namespace, page_size=_PAGE_SIZE)


async def _backfill_legacy_agent_records() -> None:
    legacy_threads, legacy_prs = await asyncio.gather(
        _all(LEGACY_THREAD_NAMESPACE), _all(LEGACY_PR_NAMESPACE)
    )
    for record in legacy_threads:
        thread_id = record.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        if record.get("source") not in AGENT_SOURCES:
            continue
        key = store_key("run", f"legacy:{thread_id}")
        if await get_value(AGENT_RUN_NAMESPACE, key):
            continue
        await put_value(
            AGENT_RUN_NAMESPACE,
            key,
            {
                "run_id": f"legacy:{thread_id}",
                "thread_id": thread_id,
                "github_login": normalize_login(record.get("github_login")),
                "user_email": normalize_email(record.get("user_email")),
                "model_id": record.get("model_id") or "",
                "effort": record.get("effort") or "",
                "source": record.get("source"),
                "created_at_ms": timestamp_ms(record.get("created_at_ms"))
                or timestamp_ms(record.get("updated_at_ms")),
            },
        )
    for record in legacy_prs:
        owner = record.get("owner")
        repo = record.get("repo")
        number = record.get("pr_number")
        if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
            continue
        key = store_key("pr", owner.lower(), repo.lower(), number)
        if await get_value(AGENT_PR_NAMESPACE, key):
            continue
        await put_value(
            AGENT_PR_NAMESPACE,
            key,
            {
                **record,
                "github_login": normalize_login(record.get("github_login")),
                "user_email": normalize_email(record.get("user_email")),
                "created_at_ms": timestamp_ms(record.get("created_at_ms"))
                or timestamp_ms(record.get("updated_at_ms")),
                "merged_at_ms": 0,
            },
        )


async def _backfill_legacy_reviews() -> None:
    offset = 0
    while True:
        page = await _client().threads.search(
            metadata={"kind": REVIEWER_THREAD_KIND}, limit=_PAGE_SIZE, offset=offset
        )
        threads = list(page or [])
        for thread in threads:
            metadata = thread_metadata(thread)
            thread_id = thread.get("thread_id") if isinstance(thread, Mapping) else None
            if not isinstance(thread_id, str) or not thread_id:
                continue
            findings = [item for item in metadata.get("findings") or [] if isinstance(item, dict)]
            head_sha = metadata.get("last_reviewed_sha") or metadata.get("head_sha") or ""
            reviewed_at_ms = timestamp_ms(
                metadata.get("created_at")
                or (thread.get("created_at") if isinstance(thread, Mapping) else None)
            )
            await _backfill_legacy_review(
                thread_id=thread_id,
                metadata=metadata,
                findings=findings,
                head_sha=str(head_sha),
                reviewed_at_ms=reviewed_at_ms,
            )
        if len(threads) < _PAGE_SIZE:
            return
        offset += len(threads)


async def _backfill_legacy_review(
    *,
    thread_id: str,
    metadata: dict[str, Any],
    findings: list[dict[str, Any]],
    head_sha: str,
    reviewed_at_ms: int,
) -> None:
    pr_meta = as_json_object(metadata.get("pr"))
    owner = str(pr_meta.get("owner") or "")
    repo = str(pr_meta.get("name") or "")
    pr_number = pr_meta.get("number")
    if not owner or not repo or not isinstance(pr_number, int):
        return
    review_key = store_key("review", thread_id, head_sha)
    if not await get_value(REVIEW_NAMESPACE, review_key):
        await put_value(
            REVIEW_NAMESPACE,
            review_key,
            {
                "thread_id": thread_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "findings_recorded": len(findings),
                "published_at_ms": reviewed_at_ms,
            },
        )
    for finding in findings:
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            continue
        key = store_key("finding", thread_id, finding_id)
        if await get_value(REVIEW_FINDING_NAMESPACE, key):
            continue
        status = finding.get("status") or "open"
        surfaced = finding_surfaced(finding)
        await put_value(
            REVIEW_FINDING_NAMESPACE,
            key,
            {
                "thread_id": thread_id,
                "finding_id": finding_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "severity": finding.get("severity") or "",
                "category": finding.get("category") or "",
                "status": status,
                "first_seen_sha": finding.get("first_seen_sha") or "",
                "last_confirmed_sha": finding.get("last_confirmed_sha") or "",
                "surfaced_at_ms": reviewed_at_ms if surfaced else 0,
                "human_replies": human_reply_count(finding),
                "recorded_at_ms": reviewed_at_ms,
                "updated_at_ms": reviewed_at_ms,
                "resolved_at_ms": reviewed_at_ms if status == "resolved" else 0,
                "resolved_sha": finding.get("last_confirmed_sha") or ""
                if status == "resolved"
                else "",
            },
        )


async def _backfill_legacy_usage() -> None:
    """Migrate pre-v2 usage records into the event namespaces exactly once."""
    if await get_value(BACKFILL_NAMESPACE, _BACKFILL_KEY):
        return
    async with write_lock(BACKFILL_NAMESPACE, _BACKFILL_KEY):
        if await get_value(BACKFILL_NAMESPACE, _BACKFILL_KEY):
            return
        try:
            await _backfill_legacy_agent_records()
            await _backfill_legacy_reviews()
        except Exception:  # noqa: BLE001
            # A failed migration must not take the usage page down with it: the
            # marker stays unset so the next read tries again.
            logger.warning("Legacy usage backfill failed; retrying next read", exc_info=True)
            return
        await put_value(BACKFILL_NAMESPACE, _BACKFILL_KEY, {"completed_at_ms": now_ms()})


def _aliases(records: list[dict[str, Any]]) -> dict[str, str]:
    return {
        email: login
        for record in records
        if (email := normalize_email(record.get("user_email")))
        and (login := normalize_login(record.get("github_login")))
    }


def _user_key(record: dict[str, Any], aliases: dict[str, str]) -> str | None:
    login = normalize_login(record.get("github_login")) or aliases.get(
        normalize_email(record.get("user_email")), ""
    )
    if login:
        return f"github:{login}"
    email = normalize_email(record.get("user_email"))
    return f"email:{email}" if email else None


def _new_user(key: str, record: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    login = normalize_login(record.get("github_login")) or aliases.get(
        normalize_email(record.get("user_email")), ""
    )
    email = normalize_email(record.get("user_email"))
    return {
        "key": key,
        "github_login": login,
        "email": email,
        "name": login or email.split("@", 1)[0],
        "agent_runs": 0,
        "prs_opened": 0,
        "merged_prs": 0,
        "agent_loc": 0,
        "additions": 0,
        "deletions": 0,
        "models": Counter(),
    }


def _limited_rows(
    rows: list[dict[str, Any]], current_row: dict[str, Any] | None, limit: int
) -> list[dict[str, Any]]:
    limited = rows[: min(max(limit, 1), _MAX_ROWS)]
    if current_row and all(row["rank"] != current_row["rank"] for row in limited):
        return [*limited, current_row]
    return limited


def _in_period(record: dict[str, Any], field: str, cutoff_ms: int) -> bool:
    timestamp = timestamp_ms(record.get(field))
    return timestamp > 0 and timestamp >= cutoff_ms


def _reviewer_stats(
    period: Period,
    review_records: list[dict[str, Any]],
    finding_records: list[dict[str, Any]],
    cutoff_ms: int,
    generated_at_ms: int,
) -> dict[str, Any]:
    reviews = [
        record for record in review_records if _in_period(record, "published_at_ms", cutoff_ms)
    ]
    findings = [
        record for record in finding_records if _in_period(record, "recorded_at_ms", cutoff_ms)
    ]
    surfaced = [
        record for record in finding_records if _in_period(record, "surfaced_at_ms", cutoff_ms)
    ]
    reviewed_prs = {(r.get("owner"), r.get("repo"), r.get("pr_number")) for r in reviews}
    prs_with_findings = {
        (r.get("owner"), r.get("repo"), r.get("pr_number"))
        for r in reviews
        if int(r.get("findings_recorded") or 0) > 0
    }
    addressed = [record for record in surfaced if record.get("status") == "resolved"]
    dismissed = [record for record in surfaced if record.get("status") == "dismissed"]
    unresolved = [record for record in surfaced if record.get("status") == "open"]
    severity = Counter(str(record.get("severity")) for record in findings if record.get("severity"))
    categories = Counter(
        str(record.get("category")) for record in findings if record.get("category")
    )
    return {
        "period": period,
        "reviewed_prs": len(reviewed_prs),
        "prs_with_findings": len(prs_with_findings),
        "findings_recorded": len(findings),
        "surfaced_findings": len(surfaced),
        "addressed_findings": len(addressed),
        "resolved_after_update": sum(
            1
            for record in addressed
            if record.get("resolved_sha")
            and record.get("resolved_sha") != record.get("first_seen_sha")
        ),
        "dismissed_findings": len(dismissed),
        "unresolved_surfaced_findings": len(unresolved),
        "resolution_rate": len(addressed) / len(surfaced) if surfaced else 0.0,
        "human_replies": sum(int(record.get("human_replies") or 0) for record in surfaced),
        "severity_counts": dict(severity),
        "top_categories": [
            {"name": name, "count": count} for name, count in categories.most_common(5)
        ],
        "generated_at_ms": generated_at_ms,
    }


async def list_agent_usage_leaderboard(
    *,
    period: str | None,
    limit: int,
    current_login: str | None,
    current_email: str | None,
) -> dict[str, Any]:
    """Aggregate current usage from complete, paginated telemetry."""
    normalized = _normalize_period(period)
    cache_key = (normalized, normalize_login(current_login) or normalize_email(current_email))
    cached = _USAGE_CACHE.get(cache_key)
    if cached and now_ms() - cached[0] < _USAGE_CACHE_TTL_MS:
        payload = dict(cached[1])
        payload["rows"] = _limited_rows(payload["rows"], cached[2], limit)
        return payload
    await _backfill_legacy_usage()
    cutoff_ms = _period_cutoff_ms(normalized)
    runs, prs, review_records, finding_records = await asyncio.gather(
        _all(AGENT_RUN_NAMESPACE),
        _all(AGENT_PR_NAMESPACE),
        _all(REVIEW_NAMESPACE),
        _all(REVIEW_FINDING_NAMESPACE),
    )
    aliases = _aliases(runs + prs)
    users: dict[str, dict[str, Any]] = {}

    for record in runs:
        if not _in_period(record, "created_at_ms", cutoff_ms):
            continue
        key = _user_key(record, aliases)
        if not key:
            continue
        user = users.setdefault(key, _new_user(key, record, aliases))
        user["agent_runs"] += 1
        model = record.get("model_id")
        if isinstance(model, str) and model:
            user["models"][model] += 1

    for record in prs:
        if not _in_period(record, "created_at_ms", cutoff_ms):
            continue
        key = _user_key(record, aliases)
        if not key:
            continue
        user = users.setdefault(key, _new_user(key, record, aliases))
        additions = int(record.get("additions") or 0)
        deletions = int(record.get("deletions") or 0)
        user["prs_opened"] += 1
        user["merged_prs"] += int(bool(record.get("merged")))
        user["additions"] += additions
        user["deletions"] += deletions
        user["agent_loc"] += additions + deletions

    ordered = sorted(
        users.values(),
        key=lambda user: (
            -user["merged_prs"],
            -user["agent_loc"],
            -user["prs_opened"],
            -user["agent_runs"],
            user["name"],
        ),
    )
    current_keys = {
        f"github:{normalize_login(current_login)}" if normalize_login(current_login) else "",
        f"email:{normalize_email(current_email)}" if normalize_email(current_email) else "",
    }
    rows: list[dict[str, Any]] = []
    current_row: dict[str, Any] | None = None
    for rank, user in enumerate(ordered, 1):
        models: Counter[str] = user["models"]
        is_current = user["key"] in current_keys
        row = {
            "rank": rank,
            "user": {
                "name": user["name"] if is_current or user["github_login"] else "Open SWE user",
                "github_login": user["github_login"] or None,
                "email": (user["email"] or None) if is_current else None,
            },
            "favorite_model": models.most_common(1)[0][0] if models else "default",
            **{
                key: user[key]
                for key in (
                    "agent_runs",
                    "prs_opened",
                    "merged_prs",
                    "agent_loc",
                    "additions",
                    "deletions",
                )
            },
        }
        if is_current:
            current_row = row
        if len(rows) < _MAX_ROWS:
            rows.append(row)

    generated_at_ms = now_ms()
    payload = {
        "period": normalized,
        "rows": rows,
        "total_members": len(ordered),
        "current_user_rank": current_row["rank"] if current_row else None,
        "generated_at_ms": generated_at_ms,
        "reviewer_stats": _reviewer_stats(
            normalized, review_records, finding_records, cutoff_ms, generated_at_ms
        ),
    }
    _USAGE_CACHE[cache_key] = (generated_at_ms, payload, current_row)
    result = dict(payload)
    result["rows"] = _limited_rows(rows, current_row, limit)
    return result
