"""Searching, filtering and paging the thread list behind the Agents UI."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from fastapi import HTTPException

from agent.dashboard.threads.pins import list_thread_pin_ids, pin_thread, unpin_thread
from agent.dashboard.threads.summary import (
    _DASHBOARD_SOURCE,
    _SURFACED_SOURCES,
    _assert_thread_readable,
    _is_automation_thread,
    _is_thread_resolved,
    _metadata_repo,
    _metadata_string,
    _refresh_latest_run_metadata,
    _thread_id,
    _thread_metadata,
    _thread_summary,
    _thread_timestamp_ms,
    _thread_updated_ms,
    _ThreadSortBy,
    thread_is_readable,
    thread_source,
)
from agent.utils.json_types import JsonObject, ThreadLike
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import participant_search_filters

logger = logging.getLogger(__name__)

_THREADS_SEARCH_PAGE = 50
_THREADS_PAGE_SCAN_CAP = 5000
_THREAD_LIST_SELECT = ["thread_id", "status", "metadata", "created_at", "updated_at"]
_RUN_REFRESH_CONCURRENCY = 8
_RUNNING_METADATA_STATUSES = {"pending", "running"}


def _participant_search_filters(
    login: str, *, email: str | None = None, include_all: bool = False
) -> list[dict[str, Any]]:
    if include_all:
        return [{}]
    filters = participant_search_filters(login, email)
    # Threads created before participants existed carry only these two keys, and
    # object containment cannot match them. Drop both once those threads have
    # aged out or been backfilled.
    filters.append({"github_login": login})
    if email and email.strip():
        filters.append({"triggering_user_email": email.strip().lower()})
    return filters


def _search_metadata_filter(
    search_filter: dict[str, Any],
    *,
    resolved: bool | None = None,
    source: str | None = None,
    automation_id: str | None = None,
) -> dict[str, Any]:
    metadata = dict(search_filter)
    if resolved is True:
        metadata["resolved"] = True
    if source and source != _DASHBOARD_SOURCE:
        metadata["source"] = source
    if automation_id:
        metadata["schedule_id"] = automation_id
    return metadata


async def _search_threads_batch(
    client: Any,
    metadata: JsonObject,
    *,
    limit: int,
    offset: int,
    sort_by: _ThreadSortBy = "updated_at",
) -> list[ThreadLike]:
    batch = await client.threads.search(
        metadata=metadata,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order="desc",
        select=_THREAD_LIST_SELECT,
    )
    return [thread for thread in batch or [] if isinstance(thread, Mapping)]


def _search_matches(values: Sequence[object], query: str) -> bool:
    needle = query.lower()
    return any(isinstance(value, (str, int)) and needle in str(value).lower() for value in values)


def _metadata_matches_filters(
    metadata: Mapping[str, Any],
    *,
    resolved: bool | None,
    source: str | None,
    query: str | None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
    repo: str | None = None,
    ownerless: bool = False,
) -> bool:
    """Metadata-only filters that don't require fetching the latest run."""
    thread_repo = _metadata_repo(metadata)[2]
    if repo and thread_repo.lower() != repo.lower():
        return False
    if ownerless and thread_repo:
        return False
    is_automation = _is_automation_thread(metadata)
    if scope == "interactive" and is_automation:
        return False
    if scope == "automation" and not is_automation:
        return False
    if automation_id and _metadata_string(metadata, "schedule_id") != automation_id:
        return False
    if resolved is not None and _is_thread_resolved(metadata) is not resolved:
        return False
    if source and thread_source(metadata) != source:
        return False
    if query:
        pull_requests = metadata.get("pull_requests")
        pull_requests = pull_requests if isinstance(pull_requests, list) else []
        if not _search_matches(
            [
                metadata.get("title", "Untitled agent"),
                *_metadata_repo(metadata),
                metadata.get("branch_name"),
                metadata.get("base_branch"),
                metadata.get("pr_url"),
                metadata.get("pr_number"),
                *(
                    value
                    for record in pull_requests
                    if isinstance(record, dict)
                    for value in record.values()
                ),
            ],
            query,
        ):
            return False
    return True


def _summary_matches_filters(
    summary: dict[str, Any],
    *,
    resolved: bool | None,
    viewed: bool | None,
    source: str | None,
    status: str | None,
    query: str | None,
) -> bool:
    if resolved is not None and bool(summary.get("resolved")) is not resolved:
        return False
    if viewed is not None and bool(summary.get("viewed")) is not viewed:
        return False
    if source and summary.get("source") != source:
        return False
    if status and summary.get("status") != status:
        return False
    if query:
        pull_requests = summary.get("pullRequests")
        pull_requests = pull_requests if isinstance(pull_requests, list) else []
        pr = summary.get("pr")
        if not _search_matches(
            [
                summary.get("title"),
                summary.get("repo"),
                summary.get("repoFullName"),
                summary.get("branch"),
                *(pr.values() if isinstance(pr, dict) else ()),
                *(
                    value
                    for record in pull_requests
                    if isinstance(record, dict)
                    for value in record.values()
                ),
            ],
            query,
        ):
            return False
    return True


def _should_refresh_latest_run(thread: ThreadLike) -> bool:
    metadata = _thread_metadata(thread)
    metadata_status = metadata.get("latest_run_status")
    thread_status = thread.get("status")
    return (
        thread_status == "busy"
        or metadata_status in _RUNNING_METADATA_STATUSES
        or not isinstance(metadata_status, str)
    )


async def _summarize_thread(
    client: Any,
    thread: ThreadLike,
    *,
    refresh_active_run: bool = True,
) -> dict[str, Any]:
    latest_run_status = latest_run_id = None
    if refresh_active_run and _should_refresh_latest_run(thread):
        thread, latest_run_status, latest_run_id = await _refresh_latest_run_metadata(
            client, thread
        )
    return await _thread_summary(
        thread,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
    )


async def _summarize_threads(
    client: Any,
    threads: list[ThreadLike],
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(_RUN_REFRESH_CONCURRENCY)

    async def summarize(thread: ThreadLike) -> dict[str, Any]:
        if not _should_refresh_latest_run(thread):
            return await _summarize_thread(
                client,
                thread,
                refresh_active_run=False,
            )
        async with semaphore:
            return await _summarize_thread(
                client,
                thread,
            )

    return list(await asyncio.gather(*(summarize(thread) for thread in threads)))


async def _collect_thread_candidates(
    client: Any,
    searches: list[dict[str, Any]],
    *,
    resolved: bool | None = None,
    source: str | None = None,
    query: str | None = None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
    repo: str | None = None,
    ownerless: bool = False,
    target_per_search: int | None = None,
    surfaced_only: bool = False,
    sort_by: _ThreadSortBy = "updated_at",
) -> list[ThreadLike]:
    seen: dict[str, ThreadLike] = {}
    for search_filter in searches:
        matched_for_search = 0
        offset = 0
        metadata_filter = _search_metadata_filter(
            search_filter,
            resolved=resolved,
            source=source,
            automation_id=automation_id,
        )
        while offset < _THREADS_PAGE_SCAN_CAP:
            batch = await _search_threads_batch(
                client,
                metadata_filter,
                limit=_THREADS_SEARCH_PAGE,
                offset=offset,
                sort_by=sort_by,
            )
            if not batch:
                break
            for thread in batch:
                metadata = _thread_metadata(thread)
                if surfaced_only and thread_source(metadata) not in _SURFACED_SOURCES:
                    continue
                if not _metadata_matches_filters(
                    metadata,
                    resolved=resolved,
                    source=source,
                    query=query,
                    scope=scope,
                    automation_id=automation_id,
                    repo=repo,
                    ownerless=ownerless,
                ):
                    continue
                thread_id = _thread_id(thread)
                if not thread_id:
                    continue
                matched_for_search += 1
                seen.setdefault(thread_id, thread)
            if len(batch) < _THREADS_SEARCH_PAGE:
                break
            if target_per_search is not None and matched_for_search >= target_per_search:
                break
            offset += _THREADS_SEARCH_PAGE
    return sorted(
        seen.values(), key=lambda thread: _thread_timestamp_ms(thread, sort_by), reverse=True
    )


async def list_dashboard_threads(
    login: str, *, email: str | None = None, limit: int = 50, include_all: bool = False
) -> list[dict[str, Any]]:
    page = await list_dashboard_threads_page(
        login,
        email=email,
        limit=limit,
        offset=0,
        include_all=include_all,
    )
    return page["items"]


async def _pinned_thread_summaries(
    client: Any,
    login: str,
    email: str | None,
) -> list[dict[str, Any]]:
    async def load(thread_id: str) -> dict[str, Any] | None:
        try:
            thread = await client.threads.get(thread_id)
        except Exception:  # noqa: BLE001
            logger.debug("Could not fetch pinned sidebar thread %s", thread_id, exc_info=True)
            return None
        if not isinstance(thread, Mapping) or not thread_is_readable(_thread_metadata(thread)):
            return None
        return await _summarize_thread(client, thread)

    summaries = await asyncio.gather(
        *(load(thread_id) for thread_id in await list_thread_pin_ids(login))
    )
    return [summary for summary in summaries if summary is not None]


async def list_dashboard_pinned_threads(
    login: str,
    *,
    email: str | None = None,
) -> list[dict[str, Any]]:
    return await _pinned_thread_summaries(langgraph_client(), login, email)


async def list_dashboard_thread_projects(
    login: str,
    *,
    email: str | None = None,
    include_resolved: bool = False,
    include_automations: bool = False,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    candidates = await _collect_thread_candidates(
        langgraph_client(),
        _participant_search_filters(login, email=email, include_all=include_all),
        resolved=None if include_resolved else False,
        scope="all" if include_automations else "interactive",
    )
    projects: dict[str, dict[str, Any]] = {}
    for thread in candidates:
        _, name, full_name = _metadata_repo(_thread_metadata(thread))
        if not full_name:
            continue
        key = full_name.lower()
        updated_at = _thread_updated_ms(thread)
        current = projects.get(key)
        if current is None or updated_at > current["updatedAt"]:
            projects[key] = {
                "repoFullName": full_name,
                "name": name,
                "updatedAt": updated_at,
            }
    return sorted(projects.values(), key=lambda project: project["updatedAt"], reverse=True)


async def pin_dashboard_thread(thread_id: str, login: str) -> None:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    if not isinstance(thread, Mapping):
        raise HTTPException(404, "thread not found")
    _assert_thread_readable(_thread_metadata(thread))
    await pin_thread(login, thread_id)


async def unpin_dashboard_thread(thread_id: str, login: str) -> None:
    await unpin_thread(login, thread_id)


async def list_dashboard_threads_page(
    login: str,
    *,
    email: str | None = None,
    limit: int = 25,
    offset: int = 0,
    include_all: bool = False,
    resolved: bool | None = None,
    viewed: bool | None = None,
    source: str | None = None,
    status: str | None = None,
    query: str | None = None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
    repo: str | None = None,
    ownerless: bool = False,
    filter_participant_login: str | None = None,
    surfaced_only: bool = False,
    sort_by: _ThreadSortBy = "updated_at",
) -> dict[str, Any]:
    client = langgraph_client()
    search_login = filter_participant_login or login
    search_email = email if search_login == login else None
    searches = (
        [{"thread_category": "automation"}, {"source": "schedule"}]
        if scope == "automation" and filter_participant_login is None
        else _participant_search_filters(search_login, email=search_email, include_all=include_all)
    )
    safe_offset = max(offset, 0)
    safe_limit = min(max(limit, 1), 100)
    summary_filters = viewed is not None or status is not None
    target = None if summary_filters else safe_offset + safe_limit + 1

    candidates = await _collect_thread_candidates(
        client,
        searches,
        resolved=resolved,
        source=source,
        query=query,
        scope=scope,
        automation_id=automation_id,
        repo=repo,
        ownerless=ownerless,
        target_per_search=target,
        surfaced_only=surfaced_only,
        sort_by=sort_by,
    )

    if summary_filters:
        summaries = await _summarize_threads(
            client,
            candidates,
        )
        filtered = [
            summary
            for summary in summaries
            if _summary_matches_filters(
                summary,
                resolved=resolved,
                viewed=viewed,
                source=source,
                status=status,
                query=query,
            )
        ]
        summary_sort_field = "createdAt" if sort_by == "created_at" else "updatedAt"
        filtered.sort(key=lambda item: item.get(summary_sort_field, 0), reverse=True)
        items = filtered[safe_offset : safe_offset + safe_limit]
        has_more = len(filtered) > safe_offset + safe_limit
    else:
        window = candidates[safe_offset : safe_offset + safe_limit]
        items = await _summarize_threads(
            client,
            window,
        )
        has_more = len(candidates) > safe_offset + safe_limit

    return {"items": items, "limit": safe_limit, "offset": safe_offset, "hasMore": has_more}
