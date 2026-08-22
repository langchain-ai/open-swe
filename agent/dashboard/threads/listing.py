"""Which threads a user sees: search, filter, paginate, summarize.

The platform can only filter on exact metadata equality, so every listing here
is the same shape: ask for the widest metadata filter the query allows, page
through it, then apply the rest of the filters locally. Refreshing a thread's
newest run costs one request per thread, so it is skipped for any thread whose
cached run status is already terminal.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import HTTPException

from ...config import langgraph_client
from ...store import now_ms
from ...utils.json_types import JsonObject, ThreadLike, as_thread_dict, thread_metadata
from ...utils.timing import phase
from ..authz import (
    DASHBOARD_SOURCE,
    SURFACED_THREAD_SOURCES,
    assert_thread_readable,
    thread_is_readable,
    thread_source,
    user_owns_thread,
)
from .serialize import (
    ThreadTimestampField,
    is_automation_thread,
    is_thread_resolved,
    metadata_string,
    run_status_to_agent_status,
    thread_id_of,
    thread_run_id,
    thread_summary,
    thread_timestamp_ms,
)

logger = logging.getLogger(__name__)

_THREADS_SEARCH_PAGE = 500
_THREADS_PAGE_SCAN_CAP = 5000
_THREAD_LIST_SELECT = ["thread_id", "status", "metadata", "created_at", "updated_at"]
_RUN_REFRESH_CONCURRENCY = 8
_RUNNING_METADATA_STATUSES = {"pending", "running"}


async def latest_run_info(client: Any, thread_id: str) -> tuple[str | None, str | None]:
    try:
        runs = await client.runs.list(thread_id, limit=1)
    except Exception:  # noqa: BLE001
        logger.debug("Could not fetch latest run for thread %s", thread_id, exc_info=True)
        return None, None
    if not runs:
        return None, None
    run = runs[0]
    raw_status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
    raw_id = (
        (run.get("run_id") or run.get("id"))
        if isinstance(run, dict)
        else (getattr(run, "run_id", None) or getattr(run, "id", None))
    )
    status = raw_status.lower() if isinstance(raw_status, str) else None
    run_id = raw_id if isinstance(raw_id, str) and raw_id else None
    return status, run_id


async def refresh_latest_run_metadata(
    client: Any, thread: ThreadLike, *, timings: dict[str, float] | None = None
) -> tuple[ThreadLike, str | None, str | None]:
    record = timings if timings is not None else {}
    thread_id = thread_id_of(thread)
    if thread_id is None:
        return thread, None, None
    with phase(record, "runs_list"):
        latest_run_status, latest_run_id = await latest_run_info(client, thread_id)
    metadata = thread_metadata(thread)
    metadata_update: dict[str, Any] = {}
    if latest_run_status and latest_run_status != metadata.get("latest_run_status"):
        metadata_update["latest_run_status"] = latest_run_status
    if latest_run_id and latest_run_id != metadata.get("latest_run_id"):
        metadata_update["latest_run_id"] = latest_run_id
    if metadata_update:
        with phase(record, "thread_update"):
            try:
                await client.threads.update(thread_id=thread_id, metadata=metadata_update)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Could not persist latest run metadata for %s", thread_id, exc_info=True
                )
            else:
                thread = {**as_thread_dict(thread), "metadata": {**metadata, **metadata_update}}
    return thread, latest_run_status, latest_run_id


def _owner_search_filters(
    login: str, *, email: str | None = None, include_all: bool = False
) -> list[dict[str, Any]]:
    if include_all:
        return [{}]
    searches = [{"github_login": login}]
    if email and email.strip():
        searches.append({"triggering_user_email": email.strip().lower()})
    return searches


def _search_metadata_filter(
    owner_filter: dict[str, Any],
    *,
    resolved: bool | None = None,
    source: str | None = None,
    automation_id: str | None = None,
) -> dict[str, Any]:
    metadata = dict(owner_filter)
    if resolved is True:
        metadata["resolved"] = True
    if source and source != DASHBOARD_SOURCE:
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
    sort_by: ThreadTimestampField = "updated_at",
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


def _thread_updated_ms(thread: ThreadLike) -> int:
    return thread_timestamp_ms(thread, "updated_at")


def _metadata_matches_filters(
    metadata: Mapping[str, Any],
    *,
    resolved: bool | None,
    source: str | None,
    query: str | None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
) -> bool:
    """Metadata-only filters that don't require fetching the latest run."""
    is_automation = is_automation_thread(metadata)
    if scope == "interactive" and is_automation:
        return False
    if scope == "automation" and not is_automation:
        return False
    if automation_id and metadata_string(metadata, "schedule_id") != automation_id:
        return False
    if resolved is not None and is_thread_resolved(metadata) is not resolved:
        return False
    if source and thread_source(metadata) != source:
        return False
    if query:
        title = metadata.get("title")
        title = title if isinstance(title, str) else "Untitled agent"
        if query.lower() not in title.lower():
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
        title = summary.get("title")
        if not isinstance(title, str) or query.lower() not in title.lower():
            return False
    return True


def _should_refresh_latest_run(thread: ThreadLike) -> bool:
    metadata = thread_metadata(thread)
    metadata_status = metadata.get("latest_run_status")
    return (
        thread.get("status") == "busy"
        or metadata_status in _RUNNING_METADATA_STATUSES
        or not isinstance(metadata_status, str)
    )


async def _summarize_thread(
    client: Any,
    thread: ThreadLike,
    *,
    owner_login: str | None = None,
    owner_email: str | None = None,
    refresh_active_run: bool = True,
) -> dict[str, Any]:
    latest_run_status = latest_run_id = None
    if refresh_active_run and _should_refresh_latest_run(thread):
        thread, latest_run_status, latest_run_id = await refresh_latest_run_metadata(client, thread)
    return await thread_summary(
        thread,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
        owner_login=owner_login,
        owner_email=owner_email,
    )


async def _summarize_threads(
    client: Any,
    threads: list[ThreadLike],
    *,
    owner_login: str | None = None,
    owner_email: str | None = None,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(_RUN_REFRESH_CONCURRENCY)

    async def summarize(thread: ThreadLike) -> dict[str, Any]:
        if not _should_refresh_latest_run(thread):
            return await _summarize_thread(
                client,
                thread,
                owner_login=owner_login,
                owner_email=owner_email,
                refresh_active_run=False,
            )
        async with semaphore:
            return await _summarize_thread(
                client,
                thread,
                owner_login=owner_login,
                owner_email=owner_email,
            )

    return list(await asyncio.gather(*(summarize(thread) for thread in threads)))


async def _collect_thread_candidates(
    client: Any,
    searches: list[dict[str, Any]],
    *,
    include_all: bool,
    login: str,
    email: str | None,
    resolved: bool | None = None,
    source: str | None = None,
    query: str | None = None,
    scope: Literal["all", "interactive", "automation"] = "all",
    automation_id: str | None = None,
    target_per_search: int | None = None,
    surfaced_only: bool = False,
    sort_by: ThreadTimestampField = "updated_at",
) -> list[ThreadLike]:
    seen: dict[str, ThreadLike] = {}
    for owner_filter in searches:
        matched_for_search = 0
        offset = 0
        metadata_filter = _search_metadata_filter(
            owner_filter,
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
                metadata = thread_metadata(thread)
                if surfaced_only and thread_source(metadata) not in SURFACED_THREAD_SOURCES:
                    continue
                if not include_all and not user_owns_thread(metadata, login, email):
                    continue
                if not _metadata_matches_filters(
                    metadata,
                    resolved=resolved,
                    source=source,
                    query=query,
                    scope=scope,
                    automation_id=automation_id,
                ):
                    continue
                thread_id = thread_id_of(thread)
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
        seen.values(), key=lambda thread: thread_timestamp_ms(thread, sort_by), reverse=True
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


async def _sidebar_active_thread_summary(
    client: Any,
    active_thread_id: str | None,
    *,
    fallback_threads: Mapping[str, ThreadLike],
    visible_thread_ids: set[str],
    login: str,
    email: str | None,
    include_all: bool,
) -> tuple[dict[str, Any], bool] | None:
    if not active_thread_id or active_thread_id in visible_thread_ids:
        return None
    thread = fallback_threads.get(active_thread_id)
    if thread is None:
        try:
            fetched = await client.threads.get(active_thread_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "Could not fetch active sidebar thread %s", active_thread_id, exc_info=True
            )
            return None
        if not isinstance(fetched, Mapping):
            return None
        thread = fetched
    metadata = thread_metadata(thread)
    if not include_all and not thread_is_readable(metadata):
        return None
    summary = await _summarize_thread(
        client,
        thread,
        owner_login=login,
        owner_email=email,
    )
    return summary, is_thread_resolved(metadata)


async def list_dashboard_threads_sidebar(
    login: str,
    *,
    email: str | None = None,
    active_limit: int = 50,
    resolved_limit: int = 20,
    active_thread_id: str | None = None,
    include_automations: bool = False,
    include_all: bool = False,
    timings: dict[str, float] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    record = timings if timings is not None else {}
    count_record = counts if counts is not None else {}
    client = langgraph_client()
    searches = _owner_search_filters(login, email=email, include_all=include_all)
    safe_active_limit = min(max(active_limit, 1), 100)
    safe_resolved_limit = min(max(resolved_limit, 1), 100)
    active_target = safe_active_limit + 1
    resolved_target = safe_resolved_limit + 1
    active: dict[str, ThreadLike] = {}
    resolved_threads: dict[str, ThreadLike] = {}

    with phase(record, "search"):
        for owner_filter in searches:
            local_active = 0
            local_resolved = 0
            offset = 0
            while offset < _THREADS_PAGE_SCAN_CAP and (
                local_active < active_target or local_resolved < resolved_target
            ):
                batch = await _search_threads_batch(
                    client,
                    owner_filter,
                    limit=_THREADS_SEARCH_PAGE,
                    offset=offset,
                )
                if not batch:
                    break
                for thread in batch:
                    metadata = thread_metadata(thread)
                    if not include_all and not user_owns_thread(metadata, login, email):
                        continue
                    if not include_automations and is_automation_thread(metadata):
                        continue
                    thread_id = thread_id_of(thread)
                    if not thread_id or thread_id in active or thread_id in resolved_threads:
                        continue
                    if is_thread_resolved(metadata):
                        local_resolved += 1
                        resolved_threads[thread_id] = thread
                    else:
                        local_active += 1
                        active[thread_id] = thread
                if len(batch) < _THREADS_SEARCH_PAGE:
                    break
                offset += _THREADS_SEARCH_PAGE

    active_candidates = sorted(active.values(), key=_thread_updated_ms, reverse=True)
    resolved_candidates = sorted(resolved_threads.values(), key=_thread_updated_ms, reverse=True)
    active_window = active_candidates[:safe_active_limit]
    resolved_window = resolved_candidates[:safe_resolved_limit]
    active_ids = {thread_id for thread in active_window if (thread_id := thread_id_of(thread))}
    # The dominant cost when a user's threads have no cached run status: one
    # `runs.list` per thread, eight at a time.
    count_record["run_refreshes"] = sum(
        _should_refresh_latest_run(thread) for thread in (*active_window, *resolved_window)
    )
    count_record["threads"] = len(active_window) + len(resolved_window)
    with phase(record, "summarize"):
        active_items, resolved_items, active_thread = await asyncio.gather(
            _summarize_threads(
                client,
                active_window,
                owner_login=login,
                owner_email=email,
            ),
            _summarize_threads(
                client,
                resolved_window,
                owner_login=login,
                owner_email=email,
            ),
            _sidebar_active_thread_summary(
                client,
                active_thread_id,
                fallback_threads={**active, **resolved_threads},
                visible_thread_ids=active_ids,
                login=login,
                email=email,
                include_all=include_all,
            ),
        )
    active_has_more = len(active_candidates) > safe_active_limit
    resolved_has_more = len(resolved_candidates) > safe_resolved_limit
    if active_thread:
        active_thread_summary, is_resolved_active_thread = active_thread
        if is_resolved_active_thread:
            resolved_items = [
                active_thread_summary,
                *[item for item in resolved_items if item["id"] != active_thread_summary["id"]],
            ]
            active_items = [
                item for item in active_items if item["id"] != active_thread_summary["id"]
            ]
            if len(resolved_items) > safe_resolved_limit:
                resolved_items = resolved_items[:safe_resolved_limit]
                resolved_has_more = True
        else:
            active_items = [
                active_thread_summary,
                *[item for item in active_items if item["id"] != active_thread_summary["id"]],
            ]
            resolved_items = [
                item for item in resolved_items if item["id"] != active_thread_summary["id"]
            ]
            if len(active_items) > safe_active_limit:
                active_items = active_items[:safe_active_limit]
                active_has_more = True
    return {
        "active": {
            "items": active_items,
            "limit": safe_active_limit,
            "hasMore": active_has_more,
        },
        "resolved": {
            "items": resolved_items,
            "limit": safe_resolved_limit,
            "hasMore": resolved_has_more,
        },
    }


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
    filter_owner_login: str | None = None,
    surfaced_only: bool = False,
    sort_by: ThreadTimestampField = "updated_at",
) -> dict[str, Any]:
    client = langgraph_client()
    search_login = filter_owner_login or login
    search_email = email if search_login == login else None
    searches = _owner_search_filters(search_login, email=search_email, include_all=include_all)
    safe_offset = max(offset, 0)
    safe_limit = min(max(limit, 1), 100)
    summary_filters = viewed is not None or status is not None
    target = None if summary_filters else safe_offset + safe_limit + 1

    candidates = await _collect_thread_candidates(
        client,
        searches,
        include_all=include_all,
        login=search_login,
        email=search_email,
        resolved=resolved,
        source=source,
        query=query,
        scope=scope,
        automation_id=automation_id,
        target_per_search=target,
        surfaced_only=surfaced_only,
        sort_by=sort_by,
    )

    if summary_filters:
        summaries = await _summarize_threads(
            client,
            candidates,
            owner_login=login,
            owner_email=email,
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
            owner_login=login,
            owner_email=email,
        )
        has_more = len(candidates) > safe_offset + safe_limit

    return {"items": items, "limit": safe_limit, "offset": safe_offset, "hasMore": has_more}


async def _mark_thread_viewed(
    client: Any,
    thread_id: str,
    metadata: dict[str, Any],
    *,
    latest_run_id: str | None,
) -> dict[str, Any]:
    metadata_update: dict[str, Any] = {"last_viewed_at_ms": now_ms()}
    run_id = thread_run_id(metadata, latest_run_id)
    if run_id:
        metadata_update["last_viewed_run_id"] = run_id
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    except Exception:  # noqa: BLE001
        logger.debug("Could not mark thread %s viewed", thread_id, exc_info=True)
        return metadata
    return {**metadata, **metadata_update}


async def get_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None, mark_viewed: bool = True
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Thread lookup failed for %s", thread_id, exc_info=True)
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    assert_thread_readable(metadata)
    is_owner = user_owns_thread(metadata, login, email)

    # The transcript is hydrated client-side by the SDK (`StreamProvider` reads
    # `GET …/state` → `stream.messages`), so the detail endpoint returns
    # metadata only — no server-side message conversion.
    thread, latest_run_status, latest_run_id = await refresh_latest_run_metadata(client, thread)
    metadata = thread_metadata(thread)
    status = run_status_to_agent_status(
        thread.get("status") if isinstance(thread.get("status"), str) else "idle",
        latest_run_status
        or (
            metadata.get("latest_run_status")
            if isinstance(metadata.get("latest_run_status"), str)
            else None
        ),
    )
    if mark_viewed and is_owner and status != "running":
        metadata = await _mark_thread_viewed(
            client,
            thread_id,
            metadata,
            latest_run_id=latest_run_id,
        )
        thread = {**as_thread_dict(thread), "metadata": metadata}

    return await thread_summary(
        thread,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
        owner_login=login,
        owner_email=email,
    )
