"""Forward-looking Open SWE Agent usage telemetry."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from langgraph_sdk import get_client

from ..utils.github_app import get_github_app_installation_token

USAGE_THREAD_NAMESPACE: list[str] = ["agent_usage", "threads"]
USAGE_PR_NAMESPACE: list[str] = ["agent_usage", "prs"]

Period = Literal["7d", "30d", "all"]
_AGENT_SOURCES = frozenset({"dashboard", "github", "slack", "linear"})
_PR_REFRESH_INTERVAL_MS = 10 * 60 * 1000
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


async def _search_values(namespace: list[str], *, limit: int = 1000) -> list[dict[str, Any]]:
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
    token = await get_github_app_installation_token()
    if not token:
        return records
    now_ms = _now_ms()
    refreshed: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for record in records:
            updated_at = _coerce_int(record.get("updated_at_ms"))
            if updated_at and now_ms - updated_at < _PR_REFRESH_INTERVAL_MS:
                refreshed.append(record)
                continue
            try:
                refreshed.append(await _refresh_pr_record(client, token, record))
            except Exception:
                logger.debug("Failed to refresh usage PR record", exc_info=True)
                refreshed.append(record)
    return refreshed


async def list_agent_usage_leaderboard(
    *,
    period: str | None,
    limit: int,
    current_login: str | None,
    current_email: str | None,
) -> dict[str, Any]:
    """Build the Open SWE Agent usage leaderboard from recorded telemetry."""
    normalized_period = _normalize_period(period)
    cutoff_ms = _period_cutoff_ms(normalized_period)
    safe_limit = min(max(limit, 1), 100)
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
    current_keys = {
        _user_key(current_login, current_email),
        _user_key(current_login, None),
        _user_key(None, current_email),
    }
    rows: list[dict[str, Any]] = []
    current_user_row: dict[str, Any] | None = None
    for index, user in enumerate(sorted_users, start=1):
        model_counts: Counter[str] = user.pop("model_counts")
        favorite_model = model_counts.most_common(1)[0][0] if model_counts else "default"
        row = {
            "rank": index,
            "user": {
                "name": user.get("name") or "Unknown user",
                "github_login": user.get("github_login") or None,
                "email": user.get("email") or None,
            },
            "favorite_model": favorite_model,
            "agent_runs": user["agent_runs"],
            "prs_opened": user["prs_opened"],
            "merged_prs": user["merged_prs"],
            "agent_loc": user["agent_loc"],
            "additions": user["additions"],
            "deletions": user["deletions"],
        }
        if user["key"] in current_keys:
            current_user_row = row
        if len(rows) < safe_limit:
            rows.append(row)

    if current_user_row and all(row["rank"] != current_user_row["rank"] for row in rows):
        rows.append(current_user_row)

    return {
        "period": normalized_period,
        "rows": rows,
        "total_members": len(sorted_users),
        "current_user_rank": current_user_row["rank"] if current_user_row else None,
    }
