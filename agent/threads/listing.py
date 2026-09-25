"""Searching, filtering and paging the thread list behind the Agents UI."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.errors import NotFoundError
from langgraph_sdk.schema import ThreadSelectField
from pydantic import BaseModel

from agent.review.session import ReviewSessionMetadata
from agent.review.walkthrough import Walkthrough
from agent.threads.pins import list_thread_pin_ids, pin_thread, unpin_thread
from agent.threads.summary import (
    _SURFACED_SOURCES,
    DASHBOARD_SOURCE,
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
    assert_thread_readable,
    thread_is_readable,
    thread_is_unlisted,
    thread_source,
)
from agent.utils.json_types import JsonObject, ThreadLike, as_thread_dict
from agent.utils.thread_ops import langgraph_client
from agent.utils.thread_participants import participant_search_filters
from agent.workspaces.routing import workspace_for_repo
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG

_THREADS_SEARCH_PAGE = 50
_THREADS_PAGE_SCAN_CAP = 5000
_THREAD_LIST_SELECT: list[ThreadSelectField] = [
    "thread_id",
    "status",
    "metadata",
    "created_at",
    "updated_at",
]
_PINNED_THREADS_BATCH_SIZE = 1000
_RUN_REFRESH_CONCURRENCY = 8
_RUNNING_METADATA_STATUSES = {"pending", "running"}

logger = logging.getLogger(__name__)


class _ScoutRunStatus(BaseModel):
    status: str = ""


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
    admin_threads: bool | None = None,
) -> dict[str, Any]:
    metadata = dict(search_filter)
    if resolved is True:
        metadata["resolved"] = True
    if source and source != DASHBOARD_SOURCE:
        metadata["source"] = source
    if automation_id:
        metadata["schedule_id"] = automation_id
    if admin_threads is True:
        metadata["admin_thread"] = True
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
    admin_threads: bool | None = None,
) -> bool:
    """Metadata-only filters that don't require fetching the latest run."""
    if thread_is_unlisted(metadata):
        return False
    thread_repo = _metadata_repo(metadata)[2]
    if repo and thread_repo.lower() != repo.lower():
        return False
    if ownerless and thread_repo:
        return False
    if admin_threads is not None and (metadata.get("admin_thread") is True) is not admin_threads:
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


async def settle_review_walkthrough(client: Any, thread: ThreadLike) -> ThreadLike:
    """Record a review's walkthrough as ready or failed once its scout has stopped."""
    metadata = _thread_metadata(thread)
    review = ReviewSessionMetadata.parse(metadata)
    thread_id = _thread_id(thread)
    if review is None or review.walkthrough_state != "building" or not thread_id:
        return thread
    try:
        runs = await client.runs.list(review.scout_thread_id, limit=1)
    except NotFoundError:
        runs = []
    except Exception:
        logger.warning(
            "Could not read the review scout's runs", exc_info=True, extra={"thread_id": thread_id}
        )
        return thread
    if runs and _ScoutRunStatus.model_validate(runs[0]).status in _RUNNING_METADATA_STATUSES:
        return thread
    requested_at = datetime.fromtimestamp((review.walkthrough_requested_at_ms or 0) / 1000, UTC)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    try:
        ready = await Walkthrough.generated_since(
            review.repo_owner, review.repo_name, review.pr_number, requested_at
        )
        update: dict[str, Any] = {
            "walkthrough_state": "ready" if ready else "failed",
            "walkthrough_ready_at_ms": now_ms if ready else None,
            "updated_at_ms": now_ms,
        }
        await client.threads.update(thread_id=thread_id, metadata=update)
    except Exception:
        logger.warning(
            "Could not record the review walkthrough's outcome",
            exc_info=True,
            extra={"thread_id": thread_id},
        )
        return thread
    return {**as_thread_dict(thread), "metadata": {**metadata, **update}}


async def _summarize_thread(
    client: Any,
    thread: ThreadLike,
    *,
    refresh_active_run: bool = True,
    minimal_run_update: bool = False,
) -> dict[str, Any]:
    thread = await settle_review_walkthrough(client, thread)
    latest_run_status = latest_run_id = None
    if refresh_active_run and _should_refresh_latest_run(thread):
        thread, latest_run_status, latest_run_id = await _refresh_latest_run_metadata(
            client, thread, return_minimal=minimal_run_update
        )
    return await _thread_summary(
        thread,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
    )


async def _summarize_threads(
    client: Any,
    threads: list[ThreadLike],
    *,
    minimal_run_update: bool = False,
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
                minimal_run_update=minimal_run_update,
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
    admin_threads: bool | None = None,
    viewer_login: str | None = None,
    viewer_email: str | None = None,
    include_private: bool = True,
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
            admin_threads=admin_threads,
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
                if thread_source(metadata) == "incidents_agent":
                    continue
                review = ReviewSessionMetadata.parse(metadata)
                if review is not None and not review.owned_by(viewer_login):
                    continue
                if metadata.get("visibility", "public") != "public" and (
                    not include_private
                    or not thread_is_readable(metadata, viewer_login, viewer_email)
                ):
                    continue
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
                    admin_threads=admin_threads,
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


async def list_unresolved_dashboard_threads(
    login: str, *, email: str | None = None
) -> list[ThreadLike]:
    client = langgraph_client()
    seen: dict[str, ThreadLike] = {}
    for metadata in _participant_search_filters(login, email=email):
        offset = 0
        while batch := await _search_threads_batch(
            client, metadata, limit=_THREADS_SEARCH_PAGE, offset=offset
        ):
            for thread in batch:
                thread_metadata = _thread_metadata(thread)
                thread_id = _thread_id(thread)
                if (
                    thread_id
                    and thread_source(thread_metadata) in _SURFACED_SOURCES
                    and thread_is_readable(thread_metadata, login, email)
                    and not thread_is_unlisted(thread_metadata)
                    and not _is_thread_resolved(thread_metadata)
                ):
                    seen.setdefault(thread_id, thread)
            offset += len(batch)
    return list(seen.values())


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
    client: LangGraphClient,
    login: str,
    email: str | None,
) -> list[JsonObject]:
    pin_ids = await list_thread_pin_ids(login)
    threads_by_id: dict[str, ThreadLike] = {}
    # Search by ID independently of sidebar filters/pages, selecting no conversation
    # state. Refresh idle metadata too: external runs and visibility can change.
    for offset in range(0, len(pin_ids), _PINNED_THREADS_BATCH_SIZE):
        batch_ids = pin_ids[offset : offset + _PINNED_THREADS_BATCH_SIZE]
        threads = await client.threads.search(
            ids=batch_ids,
            limit=len(batch_ids),
            select=_THREAD_LIST_SELECT,
        )
        for thread in threads:
            thread_id = _thread_id(thread)
            if thread_id and thread_is_readable(_thread_metadata(thread), login, email):
                threads_by_id[thread_id] = thread
    # Search order is unrelated to pin order; deleted/inaccessible IDs are omitted.
    return await _summarize_threads(
        client,
        [threads_by_id[thread_id] for thread_id in pin_ids if thread_id in threads_by_id],
        minimal_run_update=True,
    )


async def list_dashboard_pinned_threads(
    login: str,
    *,
    email: str | None = None,
) -> list[dict[str, Any]]:
    return await _pinned_thread_summaries(langgraph_client(), login, email)


async def list_dashboard_thread_repos(
    login: str,
    *,
    email: str | None = None,
    include_resolved: bool = False,
    include_automations: bool = False,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    """The repositories the viewer's threads ran in, newest activity first.

    Each entry names the workspace that owns the repository so the sidebar can
    nest repositories under their workspace; an unassigned repository belongs
    to ``default``.
    """
    candidates = await _collect_thread_candidates(
        langgraph_client(),
        _participant_search_filters(login, email=email, include_all=include_all),
        viewer_login=login,
        viewer_email=email,
        resolved=None if include_resolved else False,
        scope="all" if include_automations else "interactive",
    )
    repos: dict[str, dict[str, Any]] = {}
    for thread in candidates:
        _, name, full_name = _metadata_repo(_thread_metadata(thread))
        if not full_name:
            continue
        key = full_name.lower()
        updated_at = _thread_updated_ms(thread)
        current = repos.get(key)
        if current is None or updated_at > current["updatedAt"]:
            repos[key] = {
                "repoFullName": full_name,
                "name": name,
                "updatedAt": updated_at,
            }
    for entry in repos.values():
        owner, _, repo_name = str(entry["repoFullName"]).partition("/")
        entry["workspace"] = await workspace_for_repo(owner, repo_name) or DEFAULT_WORKSPACE_SLUG
    return sorted(repos.values(), key=lambda entry: entry["updatedAt"], reverse=True)


async def pin_dashboard_thread(thread_id: str, login: str) -> None:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    if not isinstance(thread, Mapping):
        raise HTTPException(404, "thread not found")
    assert_thread_readable(_thread_metadata(thread), login)
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
    include_private: bool = True,
    surfaced_only: bool = False,
    admin_threads: bool | None = None,
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
        admin_threads=admin_threads,
        viewer_login=login,
        viewer_email=email,
        include_private=include_private,
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
