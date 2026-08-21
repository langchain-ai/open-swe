"""Open SWE Agent and Review usage telemetry."""

import asyncio
import hashlib
import json
import weakref
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from langgraph_sdk import get_client

from ..utils.json_types import as_json_object

AGENT_RUN_NAMESPACE = ["usage", "v2", "agent_runs"]
AGENT_PR_NAMESPACE = ["usage", "v2", "agent_prs"]
REVIEW_NAMESPACE = ["usage", "v2", "reviews"]
REVIEW_FINDING_NAMESPACE = ["usage", "v2", "review_findings"]

Period = Literal["7d", "30d", "all"]
_PAGE_SIZE = 1000
_AGENT_SOURCES = frozenset({"dashboard", "github", "slack", "linear", "schedule"})
_WRITE_LOCKS: weakref.WeakValueDictionary[tuple[tuple[str, ...], str, int], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_USAGE_CACHE: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
_USAGE_CACHE_TTL_MS = 60_000


def _client():
    return get_client()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


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


def _normalize_period(period: str | None) -> Period:
    if period == "7d" or period == "all":
        return period
    return "30d"


def _period_cutoff_ms(period: Period) -> int:
    days = 7 if period == "7d" else 30 if period == "30d" else 0
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000) if days else 0


def _store_key(*parts: object) -> str:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _record(item: Any) -> dict[str, Any] | None:
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _write_lock(namespace: list[str], key: str) -> asyncio.Lock:
    lock_key = (tuple(namespace), key, id(asyncio.get_running_loop()))
    lock = _WRITE_LOCKS.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _WRITE_LOCKS[lock_key] = lock
    return lock


async def _get(namespace: list[str], key: str) -> dict[str, Any] | None:
    try:
        return _record(await _client().store.get_item(namespace, key))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


async def _mutate(
    namespace: list[str], key: str, update: Callable[[dict[str, Any] | None], dict[str, Any]]
) -> None:
    async with _write_lock(namespace, key):
        await _client().store.put_item(namespace, key, update(await _get(namespace, key)))


async def _all(namespace: list[str]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    offset = 0
    while True:
        result = await _client().store.search_items(namespace, limit=_PAGE_SIZE, offset=offset)
        items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
        page = list(items or [])
        values.extend(value for item in page if (value := _record(item)) is not None)
        if len(page) < _PAGE_SIZE:
            return values
        offset += len(page)


def _login(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _email(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _int(value: object, fallback: object = 0) -> int:
    return value if isinstance(value, int) else fallback if isinstance(fallback, int) else 0


async def record_agent_run_usage(
    *,
    run_id: str,
    thread_id: str,
    github_login: str | None,
    user_email: str | None,
    model_id: str,
    effort: str | None,
    source: str | None,
) -> None:
    """Record one actual Agent run, idempotently."""
    if not run_id or not thread_id:
        return
    key = _store_key("run", run_id)
    now_ms = _now_ms()

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        return existing or {
            "run_id": run_id,
            "thread_id": thread_id,
            "github_login": _login(github_login),
            "user_email": _email(user_email),
            "model_id": model_id,
            "effort": effort or "",
            "source": source if source in _AGENT_SOURCES else "dashboard",
            "created_at_ms": now_ms,
        }

    await _mutate(AGENT_RUN_NAMESPACE, key, update)


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
    created_at: object = None,
    merged_at: object = None,
) -> None:
    """Record an Agent-authored PR while preserving its original attribution."""
    if not owner or not repo or not pr_number:
        return
    key = _store_key("pr", owner.lower(), repo.lower(), pr_number)
    now_ms = _now_ms()

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        was_merged = bool((existing or {}).get("merged"))
        value = {
            **(existing or {}),
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "pr_url": pr_url or (existing or {}).get("pr_url", ""),
            "head": head,
            "base": base,
            "additions": max(0, additions),
            "deletions": max(0, deletions),
            "changed_files": max(0, changed_files),
            "state": "closed" if was_merged else state or "open",
            "merged": was_merged or bool(merged),
            "merged_at_ms": _timestamp_ms(merged_at) or (existing or {}).get("merged_at_ms", 0),
            "updated_at_ms": now_ms,
        }
        if not existing:
            value.update(
                thread_id=thread_id or "",
                github_login=_login(github_login),
                user_email=_email(user_email),
                created_at_ms=_timestamp_ms(created_at) or now_ms,
            )
        return value

    await _mutate(AGENT_PR_NAMESPACE, key, update)


async def update_agent_pr_usage_from_webhook(payload: dict[str, Any]) -> None:
    """Update a known Agent PR from a verified GitHub webhook payload."""
    pr = payload.get("pull_request")
    repo_payload = payload.get("repository")
    if not isinstance(pr, dict) or not isinstance(repo_payload, dict):
        return
    owner_payload = repo_payload.get("owner")
    owner = owner_payload.get("login") if isinstance(owner_payload, dict) else None
    repo = repo_payload.get("name")
    number = pr.get("number")
    if not isinstance(owner, str) or not isinstance(repo, str) or not isinstance(number, int):
        return
    key = _store_key("pr", owner.lower(), repo.lower(), number)
    existing = await _get(AGENT_PR_NAMESPACE, key)
    if not existing:
        return
    await record_agent_pr_usage(
        thread_id=existing.get("thread_id") if isinstance(existing.get("thread_id"), str) else None,
        github_login=existing.get("github_login"),
        user_email=existing.get("user_email"),
        owner=owner,
        repo=repo,
        pr_number=number,
        pr_url=pr.get("html_url"),
        head=as_json_object(pr.get("head")).get("ref") or existing.get("head", ""),
        base=as_json_object(pr.get("base")).get("ref") or existing.get("base", ""),
        additions=_int(pr.get("additions"), existing.get("additions")),
        deletions=_int(pr.get("deletions"), existing.get("deletions")),
        changed_files=_int(pr.get("changed_files"), existing.get("changed_files")),
        state=pr.get("state") if isinstance(pr.get("state"), str) else existing.get("state"),
        merged=bool(pr.get("merged")),
        created_at=pr.get("created_at"),
        merged_at=pr.get("merged_at"),
    )


def _finding_surfaced(finding: Mapping[str, Any]) -> bool:
    surface = as_json_object(finding.get("surface"))
    return bool(
        surface.get("state") in {"surfaced", "resolve_pending", "resolved"}
        or isinstance(finding.get("github_review_id"), int)
        or isinstance(finding.get("github_review_comment_id"), int)
        or finding.get("github_review_comment_ids")
    )


async def record_reviewer_publication(
    *,
    thread_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    findings: Sequence[Mapping[str, Any]],
) -> None:
    """Record a completed review and its finding cohort."""
    now_ms = _now_ms()
    await _client().store.put_item(
        REVIEW_NAMESPACE,
        _store_key("review", thread_id, head_sha),
        {
            "thread_id": thread_id,
            "owner": owner,
            "repo": repo,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "findings_recorded": sum(
                1 for finding in findings if finding.get("first_seen_sha") == head_sha
            ),
            "published_at_ms": now_ms,
        },
    )
    for finding in findings:
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            continue
        key = _store_key("finding", thread_id, finding_id)
        surfaced = _finding_surfaced(finding)

        def update(
            existing: dict[str, Any] | None,
            finding: Mapping[str, Any] = finding,
            finding_id: str = finding_id,
            surfaced: bool = surfaced,
        ) -> dict[str, Any]:
            value = {
                **(existing or {}),
                "thread_id": thread_id,
                "finding_id": finding_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "severity": finding.get("severity") or "",
                "category": finding.get("category") or "",
                "status": finding.get("status") or "open",
                "first_seen_sha": finding.get("first_seen_sha") or "",
                "last_confirmed_sha": finding.get("last_confirmed_sha") or "",
                "surfaced_at_ms": (existing or {}).get("surfaced_at_ms")
                or (now_ms if surfaced else 0),
                "human_replies": _human_reply_count(finding),
                "updated_at_ms": now_ms,
            }
            if not existing:
                value["recorded_at_ms"] = now_ms
            if value["status"] == "resolved" and not value.get("resolved_at_ms"):
                value["resolved_at_ms"] = now_ms
                value["resolved_sha"] = value["last_confirmed_sha"]
            return value

        await _mutate(REVIEW_FINDING_NAMESPACE, key, update)


def _human_reply_count(finding: Mapping[str, Any]) -> int:
    interactions = finding.get("interactions")
    if isinstance(interactions, list):
        return sum(
            1
            for interaction in interactions
            if isinstance(interaction, dict) and interaction.get("kind") == "human_reply"
        )
    return int(bool(finding.get("last_human_reply_at")))


async def record_reviewer_finding_state(thread_id: str, finding: Mapping[str, Any]) -> None:
    """Update an already-published finding's outcome state."""
    finding_id = finding.get("id")
    if not isinstance(finding_id, str) or not finding_id:
        return
    key = _store_key("finding", thread_id, finding_id)
    now_ms = _now_ms()

    def update(existing: dict[str, Any] | None) -> dict[str, Any]:
        if not existing:
            return {}
        status = finding.get("status") or "open"
        value = {
            **existing,
            "status": status,
            "severity": finding.get("severity") or existing.get("severity", ""),
            "category": finding.get("category") or existing.get("category", ""),
            "last_confirmed_sha": finding.get("last_confirmed_sha") or "",
            "human_replies": _human_reply_count(finding),
            "updated_at_ms": now_ms,
        }
        if _finding_surfaced(finding) and not value.get("surfaced_at_ms"):
            value["surfaced_at_ms"] = now_ms
        if status == "resolved" and not value.get("resolved_at_ms"):
            value["resolved_at_ms"] = now_ms
            value["resolved_sha"] = value["last_confirmed_sha"]
        return value

    async with _write_lock(REVIEW_FINDING_NAMESPACE, key):
        existing = await _get(REVIEW_FINDING_NAMESPACE, key)
        if existing:
            await _client().store.put_item(REVIEW_FINDING_NAMESPACE, key, update(existing))


def _aliases(records: list[dict[str, Any]]) -> dict[str, str]:
    return {
        email: login
        for record in records
        if (email := _email(record.get("user_email")))
        and (login := _login(record.get("github_login")))
    }


def _user_key(record: dict[str, Any], aliases: dict[str, str]) -> str | None:
    login = _login(record.get("github_login")) or aliases.get(_email(record.get("user_email")), "")
    if login:
        return f"github:{login}"
    email = _email(record.get("user_email"))
    return f"email:{email}" if email else None


def _new_user(key: str, record: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    login = _login(record.get("github_login")) or aliases.get(_email(record.get("user_email")), "")
    email = _email(record.get("user_email"))
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


def _in_period(record: dict[str, Any], field: str, cutoff_ms: int) -> bool:
    timestamp = _timestamp_ms(record.get(field))
    return timestamp > 0 and timestamp >= cutoff_ms


async def list_agent_usage_leaderboard(
    *,
    period: str | None,
    limit: int,
    current_login: str | None,
    current_email: str | None,
) -> dict[str, Any]:
    """Aggregate current usage from complete, paginated telemetry."""
    normalized = _normalize_period(period)
    cache_key = (normalized, _login(current_login) or _email(current_email))
    cached = _USAGE_CACHE.get(cache_key)
    if cached and _now_ms() - cached[0] < _USAGE_CACHE_TTL_MS:
        payload = dict(cached[1])
        payload["rows"] = payload["rows"][: min(max(limit, 1), 100)]
        return payload
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
        f"github:{_login(current_login)}" if _login(current_login) else "",
        f"email:{_email(current_email)}" if _email(current_email) else "",
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
        if len(rows) < 100:
            rows.append(row)
    if current_row and current_row not in rows:
        rows.append(current_row)

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
    now_ms = _now_ms()
    reviewer_stats = {
        "period": normalized,
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
        "generated_at_ms": now_ms,
    }
    payload = {
        "period": normalized,
        "rows": rows,
        "total_members": len(ordered),
        "current_user_rank": current_row["rank"] if current_row else None,
        "generated_at_ms": now_ms,
        "reviewer_stats": reviewer_stats,
    }
    _USAGE_CACHE[cache_key] = (now_ms, payload)
    result = dict(payload)
    result["rows"] = rows[: min(max(limit, 1), 100)]
    return result
