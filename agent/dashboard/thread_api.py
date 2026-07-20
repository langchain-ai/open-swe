"""Dashboard thread list/detail/run/stream endpoints backed by LangGraph."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException
from langchain_core.messages.content import ImageContentBlock, create_image_block
from pydantic import BaseModel, ConfigDict, Field

from ..utils.dashboard_handoff import DASHBOARD_HANDOFF_INSTRUCTION
from ..utils.json_types import (
    JsonObject,
    ThreadLike,
    as_json_object,
    as_thread_dict,
    thread_metadata,
)
from ..utils.langsmith import get_langsmith_trace_url
from ..utils.slack import lookup_slack_thread_run_mapping, update_slack_trace_reply_for_web_handoff
from ..utils.thread_ops import (
    get_thread_active_status,
    langgraph_client,
    langgraph_url,
    queue_message_for_thread,
)
from .agent_overrides import normalize_profile_overrides
from .options import (
    SUPPORTED_MODEL_IDS,
    default_vision_model_pair,
    gate_fable_model,
    model_supports_effort,
    model_supports_images,
)
from .pr_diff import build_pr_diff_files
from .profiles import get_profile, get_valid_access_token
from .team_settings import get_team_default_model, get_team_fable_enabled
from .user_mappings import email_for_login

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
_DASHBOARD_SOURCE = "dashboard"
# Modes required for the v2 event-stream protocol (`POST …/stream/events`).
# `@langchain/react` subscribes to `messages`, `tools`, `lifecycle`, etc.;
# legacy `messages-tuple`-only runs emit almost nothing on those channels.
_DASHBOARD_STREAM_MODES: tuple[str, ...] = (
    "values",
    "updates",
    "messages",
    "messages-tuple",
    "tools",
    "checkpoints",
    "events",
)
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_MAX_DASHBOARD_IMAGES = 5
_MAX_DASHBOARD_IMAGE_BYTES = 10 * 1024 * 1024
_PROXY_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_PROXY_STREAM_TIMEOUT = httpx.Timeout(None)
# Sources whose threads should surface in the Agents UI (besides "dashboard").
_SURFACED_SOURCES: tuple[str, ...] = ("dashboard", "github", "slack", "linear", "schedule")
# PR lifecycle states surfaced to the UI for a thread's associated pull request.
_PR_STATES: frozenset[str] = frozenset({"draft", "open", "merged", "closed"})
_RECOVERY_PATCH_LIMIT_BYTES = 25 * 1024 * 1024
_RECOVERY_PATCH_TIMEOUT_SECONDS = 120


async def create_sandbox(*args: Any, **kwargs: Any) -> Any:
    # deferred: pulls deepagents -> langchain_anthropic -> anthropic at import time
    from ..utils.sandbox import create_sandbox as _create_sandbox

    return await _create_sandbox(*args, **kwargs)


def _agent_version_metadata() -> dict[str, str]:
    revision = os.environ.get("LANGCHAIN_REVISION_ID")
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


def _require_json_content_type(content_type: str) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Content-Type must be application/json")


def _langgraph_proxy_headers(
    *, content_type: str = "application/json", accept: str | None = None
) -> dict[str, str]:
    headers = {"Content-Type": content_type}
    if accept:
        headers["Accept"] = accept
    api_key = (
        os.environ.get("LANGSMITH_API_KEY")
        or os.environ.get("LANGCHAIN_API_KEY")
        or os.environ.get("LANGSMITH_API_KEY_PROD")
    )
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _thread_is_busy(thread: ThreadLike) -> bool:
    return thread.get("status") == "busy"


async def _resolve_run_email(login: str, profile: dict[str, Any]) -> str | None:
    """Email used for GitHub/LangSmith auth on a run.

    Prefers the admin/self GitHub→email mapping (the work email known to
    the org) over the OAuth profile email, which may be a personal account
    that isn't an org member.
    """
    mapped = await email_for_login(login)
    return mapped or profile.get("email")


class DashboardImageBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str | None = None
    base64: str = Field(min_length=1)
    mime_type: str = Field(alias="mimeType", min_length=1)
    file_name: str | None = Field(default=None, alias="fileName")


class ThreadMessageBody(BaseModel):
    content: str = Field(default="", max_length=20_000)
    images: list[DashboardImageBody] = Field(default_factory=list)
    model_id: str | None = None
    effort: str | None = None
    plan_mode: bool = False


class ThreadResolveBody(BaseModel):
    resolved: bool = True


def _normalize_model_choice(
    model_id: str | None, effort: str | None
) -> tuple[str | None, str | None]:
    if not isinstance(model_id, str) or model_id not in SUPPORTED_MODEL_IDS:
        return None, None
    if not isinstance(effort, str) or not model_supports_effort(model_id, effort):
        return None, None
    return model_id, effort


async def _resolve_agent_model_choice(
    profile: dict[str, Any],
    model_id: str | None,
    effort: str | None,
) -> tuple[str, str]:
    resolved_model, resolved_effort = await get_team_default_model("agent")
    profile_model, profile_effort = normalize_profile_overrides(profile)
    if profile_model and profile_effort:
        resolved_model, resolved_effort = profile_model, profile_effort
    chosen_model, chosen_effort = _normalize_model_choice(model_id, effort)
    if chosen_model and chosen_effort:
        resolved_model, resolved_effort = chosen_model, chosen_effort
    resolved_model, resolved_effort = gate_fable_model(
        resolved_model, resolved_effort, fable_enabled=await get_team_fable_enabled()
    )
    if not isinstance(resolved_effort, str):
        raise ValueError("team default model must include a reasoning effort")
    return resolved_model, resolved_effort


def _with_vision_fallback(model_id: str, effort: str, *, has_images: bool) -> tuple[str, str]:
    if not has_images or model_supports_images(model_id):
        return model_id, effort
    fallback_model_id, fallback_effort = default_vision_model_pair()
    logger.info(
        "Using vision fallback model %s for dashboard image input; configured model %s "
        "does not support images",
        fallback_model_id,
        model_id,
    )
    return fallback_model_id, fallback_effort


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _parse_repo(full_name: str | None) -> dict[str, str] | None:
    if not isinstance(full_name, str):
        return None
    parts = full_name.strip().split("/", 1)
    if len(parts) != 2:
        return None
    owner, name = parts[0].strip(), parts[1].strip()
    if not owner or not name:
        return None
    return {"owner": owner, "name": name}


def _decode_dashboard_image(image: DashboardImageBody) -> bytes:
    if image.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
        raise HTTPException(422, f"unsupported image type: {image.mime_type}")
    try:
        data = base64.b64decode(image.base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(422, "invalid image data") from exc
    if len(data) > _MAX_DASHBOARD_IMAGE_BYTES:
        raise HTTPException(422, "image exceeds 10MB limit")
    return data


def _image_blocks(
    images: list[DashboardImageBody], *, model_id: str | None
) -> list[ImageContentBlock]:
    if len(images) > _MAX_DASHBOARD_IMAGES:
        raise HTTPException(422, f"at most {_MAX_DASHBOARD_IMAGES} images are supported")
    if images and (not model_id or not model_supports_images(model_id)):
        model_label = model_id or "the current model"
        raise HTTPException(422, f"model {model_label} does not support image input")
    return [
        create_image_block(
            base64=base64.b64encode(_decode_dashboard_image(image)).decode("ascii"),
            mime_type=image.mime_type,
        )
        for image in images
    ]


def _user_message_content(
    prompt: str, images: list[DashboardImageBody], *, model_id: str | None = None
) -> str | list[ImageContentBlock | dict[str, str]]:
    text = prompt.strip()
    if not text and not images:
        raise HTTPException(422, "prompt or image required")
    if not images:
        return text
    return [
        *_image_blocks(images, model_id=model_id),
        *([{"type": "text", "text": text}] if text else []),
    ]


async def _ensure_dashboard_github_token(login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")


def _thread_owner_login(metadata: Mapping[str, Any]) -> str | None:
    login = metadata.get("github_login")
    return login.strip() if isinstance(login, str) and login.strip() else None


def _thread_owner_email(metadata: Mapping[str, Any]) -> str | None:
    email = metadata.get("triggering_user_email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def _thread_source(metadata: Mapping[str, Any]) -> str:
    source = metadata.get("source")
    return source if isinstance(source, str) and source else _DASHBOARD_SOURCE


def _metadata_model_id(metadata: Mapping[str, Any]) -> str | None:
    for key in ("resolved_model", "model"):
        model = metadata.get(key)
        if isinstance(model, str) and model in SUPPORTED_MODEL_IDS:
            return model
    return None


def _user_owns_thread(metadata: Mapping[str, Any], login: str, email: str | None) -> bool:
    if _thread_source(metadata) not in _SURFACED_SOURCES:
        return False
    if _thread_owner_login(metadata) == login:
        return True
    if email and _thread_owner_email(metadata) == email.strip().lower():
        return True
    return False


def _assert_thread_owner(metadata: Mapping[str, Any], login: str, email: str | None = None) -> None:
    if not _user_owns_thread(metadata, login, email):
        raise HTTPException(404, "thread not found")


def _attribution_prefix(metadata: Mapping[str, Any], login: str, email: str | None) -> str:
    """Attribution prefix for a message; empty when the poster owns the thread.

    Teammates can post into any surfaced-source thread (read access is already
    org-gated). Their messages are tagged with the verified session login so the
    agent and the thread owner can tell who sent them.
    """
    if _user_owns_thread(metadata, login, email):
        return ""
    return f"@{login}: "


def _thread_is_readable(metadata: Mapping[str, Any]) -> bool:
    """Any surfaced-source thread is readable by authenticated users.

    Dashboard login is already gated by ``ALLOWED_GITHUB_ORGS`` (see
    ``oauth.enforce_org_login_gate``), so any logged-in user is a trusted
    org member. This lets teammates open "Open in Web" links shared in Slack
    threads with read-only access.
    """
    return _thread_source(metadata) in _SURFACED_SOURCES


def _assert_thread_readable(metadata: Mapping[str, Any]) -> None:
    if not _thread_is_readable(metadata):
        raise HTTPException(404, "thread not found")


def _metadata_repo(metadata: Mapping[str, Any]) -> tuple[str, str, str]:
    owner = metadata.get("repo_owner")
    name = metadata.get("repo_name")
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return owner, name, f"{owner}/{name}"
    repo = metadata.get("repo")
    if isinstance(repo, dict):
        o = repo.get("owner")
        n = repo.get("name")
        if isinstance(o, str) and isinstance(n, str) and o and n:
            return o, n, f"{o}/{n}"
    return "", "", ""


def _run_status_to_agent_status(thread_status: str | None, run_status: str | None) -> str:
    if thread_status == "busy" or run_status in {"pending", "running"}:
        return "running"
    if run_status in {"error", "failed", "timeout", "interrupted"}:
        return "error"
    if run_status == "success":
        return "finished"
    return "idle"


def _thread_run_id(metadata: Mapping[str, Any], latest_run_id: str | None) -> str | None:
    if latest_run_id:
        return latest_run_id
    run_id = metadata.get("latest_run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def _is_thread_viewed(metadata: Mapping[str, Any], latest_run_id: str | None) -> bool:
    viewed_at = metadata.get("last_viewed_at_ms")
    viewed_run_id = metadata.get("last_viewed_run_id")
    run_id = _thread_run_id(metadata, latest_run_id)
    if run_id:
        return viewed_run_id == run_id
    return isinstance(viewed_at, (int, float))


def _is_thread_resolved(metadata: Mapping[str, Any]) -> bool:
    return metadata.get("resolved") is True


def _thread_summary(
    thread: ThreadLike,
    *,
    latest_run_status: str | None = None,
    latest_run_id: str | None = None,
    owner_login: str | None = None,
    owner_email: str | None = None,
) -> dict[str, Any]:
    metadata = thread_metadata(thread)
    owner, name, full_name = _metadata_repo(metadata)
    created_at = metadata.get("created_at_ms")
    updated_at = metadata.get("updated_at_ms")
    title = metadata.get("title") if isinstance(metadata.get("title"), str) else "Untitled agent"
    model = metadata.get("model") if isinstance(metadata.get("model"), str) else "Default"
    effort = metadata.get("effort") if isinstance(metadata.get("effort"), str) else None
    thread_status = thread.get("status") if isinstance(thread.get("status"), str) else "idle"
    metadata_run_status = metadata.get("latest_run_status")
    run_status = latest_run_status or (
        metadata_run_status if isinstance(metadata_run_status, str) else None
    )
    status = _run_status_to_agent_status(thread_status, run_status)

    pr_number = metadata.get("pr_number")
    pr_url = metadata.get("pr_url")
    pr_title = metadata.get("pr_title")
    pr_state = metadata.get("pr_state")

    thread_id = thread.get("thread_id") or thread.get("id")
    trace_url = get_langsmith_trace_url(thread_id) if isinstance(thread_id, str) else None

    raw_sandbox_id = metadata.get("sandbox_id")
    # "__creating__" is the in-flight sentinel written before the real id lands.
    sandbox_id = (
        raw_sandbox_id
        if isinstance(raw_sandbox_id, str) and raw_sandbox_id and raw_sandbox_id != "__creating__"
        else None
    )

    summary: dict[str, Any] = {
        "id": thread_id,
        "title": title,
        "repo": name,
        "repoFullName": full_name,
        "branch": metadata.get("branch_name") or metadata.get("base_branch") or "main",
        "model": model,
        "effort": effort,
        "planMode": metadata.get("plan_mode") is True,
        "planStatus": metadata.get("plan_status"),
        "source": _thread_source(metadata),
        "status": status,
        "viewed": _is_thread_viewed(metadata, latest_run_id),
        "viewedAt": (
            int(metadata["last_viewed_at_ms"])
            if isinstance(metadata.get("last_viewed_at_ms"), (int, float))
            else None
        ),
        "resolved": _is_thread_resolved(metadata),
        "resolvedAt": (
            int(metadata["resolved_at_ms"])
            if isinstance(metadata.get("resolved_at_ms"), (int, float))
            else None
        ),
        "createdAt": int(created_at) if isinstance(created_at, (int, float)) else _now_ms(),
        "updatedAt": int(updated_at) if isinstance(updated_at, (int, float)) else _now_ms(),
        "isOwner": (_user_owns_thread(metadata, owner_login, owner_email) if owner_login else True),
        "traceUrl": trace_url,
        "sandboxId": sandbox_id,
    }
    if isinstance(pr_number, int) and isinstance(pr_url, str):
        summary["pr"] = {
            "number": pr_number,
            "title": pr_title if isinstance(pr_title, str) else title,
            "state": pr_state if pr_state in _PR_STATES else "open",
            "headRef": metadata.get("branch_name") or "",
            "baseRef": metadata.get("base_branch") or "main",
            "url": pr_url,
        }
    diff_stats = as_json_object(metadata.get("diff_stats"))
    if diff_stats:
        summary["diffStats"] = {
            "files": int(diff_stats.get("files") or 0),
            "additions": int(diff_stats.get("additions") or 0),
            "deletions": int(diff_stats.get("deletions") or 0),
        }
    # The transcript hydrates client-side from the SDK (`GET …/state` →
    # `stream.messages`); the summary only carries metadata.
    summary["messages"] = []
    return summary


async def _latest_run_info(client: Any, thread_id: str) -> tuple[str | None, str | None]:
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


async def _latest_run_status(thread_id: str) -> str | None:
    status, _ = await _latest_run_info(langgraph_client(), thread_id)
    return status


async def _refresh_latest_run_metadata(
    client: Any, thread: ThreadLike
) -> tuple[ThreadLike, str | None, str | None]:
    thread_id = thread.get("thread_id") or thread.get("id")
    if not isinstance(thread_id, str) or not thread_id:
        return thread, None, None
    latest_run_status, latest_run_id = await _latest_run_info(client, thread_id)
    metadata = thread_metadata(thread)
    metadata_update: dict[str, Any] = {}
    if latest_run_status and latest_run_status != metadata.get("latest_run_status"):
        metadata_update["latest_run_status"] = latest_run_status
    if latest_run_id and latest_run_id != metadata.get("latest_run_id"):
        metadata_update["latest_run_id"] = latest_run_id
    if metadata_update:
        try:
            await client.threads.update(thread_id=thread_id, metadata=metadata_update)
        except Exception:  # noqa: BLE001
            logger.debug("Could not persist latest run metadata for %s", thread_id, exc_info=True)
        else:
            thread = {**as_thread_dict(thread), "metadata": {**metadata, **metadata_update}}
    return thread, latest_run_status, latest_run_id


_THREADS_SEARCH_PAGE = 500
_THREADS_PAGE_SCAN_CAP = 5000
_THREAD_LIST_SELECT = ["thread_id", "status", "metadata", "updated_at"]
_RUN_REFRESH_CONCURRENCY = 8
_RUNNING_METADATA_STATUSES = {"pending", "running"}


def _thread_id(thread: ThreadLike) -> str | None:
    thread_id = thread.get("thread_id") or thread.get("id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _thread_metadata(thread: ThreadLike) -> JsonObject:
    return thread_metadata(thread)


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
    owner_filter: dict[str, Any], *, resolved: bool | None = None, source: str | None = None
) -> dict[str, Any]:
    metadata = dict(owner_filter)
    if resolved is True:
        metadata["resolved"] = True
    if source and source != _DASHBOARD_SOURCE:
        metadata["source"] = source
    return metadata


async def _search_threads_batch(
    client: Any, metadata: JsonObject, *, limit: int, offset: int
) -> list[ThreadLike]:
    batch = await client.threads.search(
        metadata=metadata,
        limit=limit,
        offset=offset,
        sort_by="updated_at",
        sort_order="desc",
        select=_THREAD_LIST_SELECT,
    )
    return [thread for thread in batch or [] if isinstance(thread, Mapping)]


def _thread_updated_ms(thread: ThreadLike) -> int:
    metadata = _thread_metadata(thread)
    value = metadata.get("updated_at_ms")
    if isinstance(value, (int, float)):
        return int(value)
    updated_at = thread.get("updated_at")
    if isinstance(updated_at, str) and updated_at:
        try:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return 0
        return int(parsed.timestamp() * 1000)
    return 0


def _metadata_matches_filters(
    metadata: Mapping[str, Any],
    *,
    resolved: bool | None,
    source: str | None,
    query: str | None,
) -> bool:
    """Metadata-only filters that don't require fetching the latest run."""
    if resolved is not None and _is_thread_resolved(metadata) is not resolved:
        return False
    if source and _thread_source(metadata) != source:
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
    owner_login: str | None = None,
    owner_email: str | None = None,
    refresh_active_run: bool = True,
) -> dict[str, Any]:
    latest_run_status = latest_run_id = None
    if refresh_active_run and _should_refresh_latest_run(thread):
        thread, latest_run_status, latest_run_id = await _refresh_latest_run_metadata(
            client, thread
        )
    return _thread_summary(
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
    target_per_search: int | None = None,
) -> list[ThreadLike]:
    seen: dict[str, ThreadLike] = {}
    for owner_filter in searches:
        matched_for_search = 0
        offset = 0
        metadata_filter = _search_metadata_filter(owner_filter, resolved=resolved, source=source)
        while offset < _THREADS_PAGE_SCAN_CAP:
            batch = await _search_threads_batch(
                client,
                metadata_filter,
                limit=_THREADS_SEARCH_PAGE,
                offset=offset,
            )
            if not batch:
                break
            for thread in batch:
                metadata = _thread_metadata(thread)
                if not include_all and not _user_owns_thread(metadata, login, email):
                    continue
                if not _metadata_matches_filters(
                    metadata,
                    resolved=resolved,
                    source=source,
                    query=query,
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
    return sorted(seen.values(), key=_thread_updated_ms, reverse=True)


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


async def list_dashboard_threads_sidebar(
    login: str,
    *,
    email: str | None = None,
    active_limit: int = 50,
    resolved_limit: int = 20,
    include_all: bool = False,
) -> dict[str, Any]:
    client = langgraph_client()
    searches = _owner_search_filters(login, email=email, include_all=include_all)
    safe_active_limit = min(max(active_limit, 1), 100)
    safe_resolved_limit = min(max(resolved_limit, 1), 100)
    active_target = safe_active_limit + 1
    resolved_target = safe_resolved_limit + 1
    active: dict[str, ThreadLike] = {}
    resolved_threads: dict[str, ThreadLike] = {}

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
                metadata = _thread_metadata(thread)
                if not include_all and not _user_owns_thread(metadata, login, email):
                    continue
                thread_id = _thread_id(thread)
                if not thread_id or thread_id in active or thread_id in resolved_threads:
                    continue
                if _is_thread_resolved(metadata):
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
    active_items, resolved_items = await asyncio.gather(
        _summarize_threads(
            client,
            active_window,
            owner_login=None if include_all else login,
            owner_email=None if include_all else email,
        ),
        _summarize_threads(
            client,
            resolved_window,
            owner_login=None if include_all else login,
            owner_email=None if include_all else email,
        ),
    )
    return {
        "active": {
            "items": active_items,
            "limit": safe_active_limit,
            "hasMore": len(active_candidates) > safe_active_limit,
        },
        "resolved": {
            "items": resolved_items,
            "limit": safe_resolved_limit,
            "hasMore": len(resolved_candidates) > safe_resolved_limit,
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
) -> dict[str, Any]:
    client = langgraph_client()
    searches = _owner_search_filters(login, email=email, include_all=include_all)
    safe_offset = max(offset, 0)
    safe_limit = min(max(limit, 1), 100)
    summary_filters = viewed is not None or status is not None
    target = None if summary_filters else safe_offset + safe_limit + 1

    candidates = await _collect_thread_candidates(
        client,
        searches,
        include_all=include_all,
        login=login,
        email=email,
        resolved=resolved,
        source=source,
        query=query,
        target_per_search=target,
    )

    if summary_filters:
        summaries = await _summarize_threads(
            client,
            candidates,
            owner_login=None if include_all else login,
            owner_email=None if include_all else email,
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
        filtered.sort(key=lambda item: item.get("updatedAt", 0), reverse=True)
        items = filtered[safe_offset : safe_offset + safe_limit]
        has_more = len(filtered) > safe_offset + safe_limit
    else:
        window = candidates[safe_offset : safe_offset + safe_limit]
        items = await _summarize_threads(
            client,
            window,
            owner_login=None if include_all else login,
            owner_email=None if include_all else email,
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
    now_ms = _now_ms()
    metadata_update: dict[str, Any] = {"last_viewed_at_ms": now_ms}
    run_id = _thread_run_id(metadata, latest_run_id)
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
    _assert_thread_readable(metadata)
    is_owner = _user_owns_thread(metadata, login, email)

    # The transcript is hydrated client-side by the SDK (`StreamProvider` reads
    # `GET …/state` → `stream.messages`), so the detail endpoint returns
    # metadata only — no server-side message conversion.
    thread, latest_run_status, latest_run_id = await _refresh_latest_run_metadata(client, thread)
    metadata = thread_metadata(thread)
    status = _run_status_to_agent_status(
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

    return _thread_summary(
        thread,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
        owner_login=login,
        owner_email=email,
    )


def _resolve_repo_config(repo: str | None) -> dict[str, str]:
    """Resolve the run's repo from the request, or ``{}`` when none is given."""
    return _parse_repo(repo) or {}


async def _create_dashboard_thread_record(
    thread_id: str,
    *,
    login: str,
    repo_config: dict[str, str],
    repo_explicitly_none: bool = False,
    prompt: str,
    images: list[DashboardImageBody] | None = None,
    title: str | None = None,
    model_id: str | None = None,
    effort: str | None = None,
    plan_mode: bool = False,
) -> dict[str, Any]:
    """Create or update dashboard thread metadata without starting a run."""
    profile = await get_profile(login) or {}
    now_ms = _now_ms()
    prompt = prompt.strip()
    resolved_model, resolved_effort = await _resolve_agent_model_choice(profile, model_id, effort)
    resolved_model, resolved_effort = _with_vision_fallback(
        resolved_model,
        resolved_effort,
        has_images=bool(images),
    )
    _user_message_content(prompt, images or [], model_id=resolved_model)
    chosen_model, chosen_effort = _normalize_model_choice(model_id, effort)
    metadata_model = chosen_model or profile.get("default_model") or "Default"
    metadata_effort = chosen_effort or profile.get("reasoning_effort")
    if images and not model_supports_images(str(metadata_model)):
        metadata_model = resolved_model
        metadata_effort = resolved_effort
    has_repo = bool(repo_config.get("owner") and repo_config.get("name"))
    metadata: dict[str, Any] = {
        "source": _DASHBOARD_SOURCE,
        "github_login": login,
        "title": title or prompt[:80] or "New agent",
        "base_branch": profile.get("base_branch") or "main",
        "branch_prefix": profile.get("branch_prefix"),
        "model": metadata_model,
        "effort": metadata_effort,
        "resolved_model": resolved_model,
        "resolved_effort": resolved_effort,
        "plan_mode": plan_mode,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }
    if has_repo:
        metadata["repo_owner"] = repo_config["owner"]
        metadata["repo_name"] = repo_config["name"]
    elif repo_explicitly_none:
        metadata["repo_explicitly_none"] = True

    client = langgraph_client()
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)
    thread = await client.threads.get(thread_id)
    return as_thread_dict(thread)


def _repo_config_from_metadata(metadata: Mapping[str, Any]) -> dict[str, str]:
    owner, name, _ = _metadata_repo(metadata)
    if owner and name:
        return {"owner": owner, "name": name}
    return {}


async def _build_dashboard_configurable(
    thread_id: str,
    login: str,
    metadata: Mapping[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile if profile is not None else await get_profile(login) or {}
    thread_source = _thread_source(metadata)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": thread_source,
        "github_login": login,
        "user_email": await _resolve_run_email(login, profile),
    }
    repo_config = _repo_config_from_metadata(metadata)
    if repo_config:
        configurable["repo"] = repo_config
    elif metadata.get("repo_explicitly_none") is True:
        configurable["repo_explicitly_none"] = True
    source_context = metadata.get("source_context")
    if isinstance(source_context, dict):
        for key, value in source_context.items():
            configurable.setdefault(key, value)
    if metadata.get("plan_mode") is True:
        configurable["plan_mode"] = True
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                configurable[key] = value
    return configurable


def _extract_run_id_from_command_response(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for candidate in (
        payload.get("run_id"),
        payload.get("result", {}).get("run_id")
        if isinstance(payload.get("result"), dict)
        else None,
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _command_message_content(params: dict[str, Any]) -> Any:
    """The most recent user message content from a ``run.start`` command."""
    run_input = params.get("input")
    if not isinstance(run_input, dict):
        return None
    messages = run_input.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    last = messages[-1]
    return last.get("content") if isinstance(last, dict) else None


def _set_command_last_message_content(params: dict[str, Any], content: Any) -> None:
    run_input = params.get("input")
    if not isinstance(run_input, dict):
        return
    messages = run_input.get("messages")
    if not isinstance(messages, list) or not messages:
        return
    last = messages[-1]
    if isinstance(last, dict):
        last["content"] = content


def _prefix_message_content(content: Any, prefix: str) -> Any:
    if not prefix:
        return content
    if isinstance(content, str):
        return f"{prefix}{content}"
    if isinstance(content, list):
        return [{"type": "text", "text": prefix.rstrip()}, *content]
    return content


def _prepend_message_content_block(content: Any, text: str) -> Any:
    block = {"type": "text", "text": text}
    if isinstance(content, str):
        return [block, {"type": "text", "text": content}]
    if isinstance(content, list):
        return [block, *content]
    if content is None:
        return [block]
    return content


def _command_prompt_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(text for text in texts if isinstance(text, str)).strip()
    return ""


def _dashboard_images_from_content(content: Any) -> list[DashboardImageBody]:
    """Reconstruct typed image bodies from a command's message content blocks.

    The client sends image blocks as ``{"type": "image", "base64", "mime_type",
    "file_name"}`` (see the prompt bar). Rebuilding them lets
    the shared ``_create_dashboard_thread_record`` validate size/type/model.
    """
    if not isinstance(content, list):
        return []
    images: list[DashboardImageBody] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        data = block.get("base64")
        mime = block.get("mime_type") or block.get("mimeType")
        if not isinstance(data, str) or not isinstance(mime, str):
            raise HTTPException(422, "invalid image data")
        file_name = block.get("file_name") or block.get("fileName")
        images.append(
            DashboardImageBody(
                base64=data,
                mimeType=mime,
                fileName=file_name if isinstance(file_name, str) else None,
            )
        )
    return images


def _validate_command_images(content: Any, *, model_id: str | None) -> None:
    """Reject images for text-only models / oversize attachments (raises 422)."""
    images = _dashboard_images_from_content(content)
    if images:
        _image_blocks(images, model_id=model_id)


async def _enrich_run_start_command(
    thread_id: str,
    login: str,
    command: dict[str, Any],
    *,
    metadata: dict[str, Any],
    thread_busy: bool = False,
    creating: bool = False,
    email: str | None = None,
) -> dict[str, Any]:
    if command.get("method") != "run.start":
        return command

    if thread_busy:
        raise HTTPException(409, "thread is already running; queue message instead")

    client = langgraph_client()
    params = command.get("params")
    if not isinstance(params, dict):
        params = {}
        command["params"] = params

    await _ensure_dashboard_github_token(login)

    client_config = params.get("config")
    if not isinstance(client_config, dict):
        client_config = {}
    client_configurable = client_config.get("configurable")
    if not isinstance(client_configurable, dict):
        client_configurable = {}

    chosen_model, chosen_effort = _normalize_model_choice(
        client_configurable.get("agent_model_id"),
        client_configurable.get("agent_effort"),
    )
    plan_mode_requested = client_configurable.get("plan_mode") is True
    content = _command_message_content(params)
    command_images = _dashboard_images_from_content(content)
    overrides: dict[str, Any] = {}

    if creating:
        # First ``run.start`` for a client-minted thread id: stamp the full
        # dashboard thread record (owner, title, repo, model) and validate any
        # attached images against the resolved model before the run is
        # forwarded to LangGraph. The repo hint rides in the client
        # configurable; it never reaches the run config (which is rebuilt from
        # the stamped metadata below).
        thread = await _create_dashboard_thread_record(
            thread_id,
            login=login,
            repo_config=_parse_repo(client_configurable.get("repo")) or {},
            repo_explicitly_none=client_configurable.get("repo_explicitly_none") is True,
            prompt=_command_prompt_text(content),
            images=command_images,
            model_id=client_configurable.get("agent_model_id"),
            effort=client_configurable.get("agent_effort"),
            plan_mode=plan_mode_requested,
        )
        metadata = thread_metadata(thread)
        if command_images:
            resolved_model = metadata.get("resolved_model")
            resolved_effort = metadata.get("resolved_effort")
            if isinstance(resolved_model, str) and isinstance(resolved_effort, str):
                overrides["agent_model_id"] = resolved_model
                overrides["agent_effort"] = resolved_effort
        elif chosen_model and chosen_effort:
            overrides["agent_model_id"] = chosen_model
            overrides["agent_effort"] = chosen_effort
    else:
        run_model = chosen_model or _metadata_model_id(metadata)
        run_effort = chosen_effort
        if not run_effort:
            for key in ("resolved_effort", "effort"):
                value = metadata.get(key)
                if isinstance(value, str):
                    run_effort = value
                    break
        if command_images and run_model and run_effort:
            run_model, run_effort = _with_vision_fallback(run_model, run_effort, has_images=True)
        _validate_command_images(content, model_id=run_model)
        prefix = _attribution_prefix(metadata, login, email)
        if prefix:
            content = _prefix_message_content(content, prefix)
        if metadata.get("source") == "slack":
            content = _prepend_message_content_block(content, DASHBOARD_HANDOFF_INSTRUCTION)
        _set_command_last_message_content(params, content)
        metadata_update: dict[str, Any] = {"plan_mode": plan_mode_requested}
        if command_images and run_model and run_effort:
            overrides["agent_model_id"] = run_model
            overrides["agent_effort"] = run_effort
            metadata_update["model"] = run_model
            metadata_update["effort"] = run_effort
            metadata_update["resolved_model"] = run_model
            metadata_update["resolved_effort"] = run_effort
        elif chosen_model and chosen_effort:
            overrides["agent_model_id"] = chosen_model
            overrides["agent_effort"] = chosen_effort
            metadata_update["model"] = chosen_model
            metadata_update["effort"] = chosen_effort
        if _is_thread_resolved(metadata):
            metadata_update["resolved"] = False
            metadata_update["resolved_at_ms"] = None
        if metadata_update:
            metadata_update["updated_at_ms"] = _now_ms()
            metadata = {**metadata, **metadata_update}
            await client.threads.update(thread_id=thread_id, metadata=metadata)

    merged_configurable = await _build_dashboard_configurable(
        thread_id,
        login,
        metadata,
        overrides=overrides,
    )

    run_metadata = params.get("metadata")
    if not isinstance(run_metadata, dict):
        run_metadata = {}
    run_metadata = {**run_metadata, **_agent_version_metadata()}

    params["assistant_id"] = _ASSISTANT_ID
    params.setdefault("stream_mode", list(_DASHBOARD_STREAM_MODES))
    params.setdefault("stream_resumable", True)
    params["config"] = {**client_config, "configurable": merged_configurable}
    params["metadata"] = run_metadata
    command["params"] = params
    return command


def _slack_thread_context(metadata: Mapping[str, Any]) -> JsonObject | None:
    source_context = metadata.get("source_context")
    if not isinstance(source_context, dict):
        return None
    slack_thread = source_context.get("slack_thread")
    return slack_thread if isinstance(slack_thread, dict) else None


async def _notify_slack_web_handoff(
    thread_id: str, metadata: Mapping[str, Any], client: Any
) -> None:
    if metadata.get("source") != "slack":
        return
    slack_thread = _slack_thread_context(metadata)
    if not slack_thread:
        return
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not channel_id:
        return
    if not isinstance(thread_ts, str) or not thread_ts:
        return

    trace_message_ts = slack_thread.get("trace_message_ts")
    if not isinstance(trace_message_ts, str) or not trace_message_ts:
        mapping = await lookup_slack_thread_run_mapping(client, channel_id, thread_ts)
        if isinstance(mapping, dict):
            candidate = mapping.get("trace_message_ts")
            if isinstance(candidate, str) and candidate:
                trace_message_ts = candidate
    if not isinstance(trace_message_ts, str) or not trace_message_ts:
        logger.info(
            "Skipping Slack web handoff update for thread %s: missing trace message ts", thread_id
        )
        return

    await update_slack_trace_reply_for_web_handoff(channel_id, trace_message_ts, thread_id)


async def send_dashboard_message(
    thread_id: str, login: str, body: ThreadMessageBody, *, email: str | None = None
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    _assert_thread_readable(metadata)

    prompt = f"{_attribution_prefix(metadata, login, email)}{body.content.strip()}"
    now_ms = _now_ms()
    chosen_model, chosen_effort = _normalize_model_choice(body.model_id, body.effort)
    handoff_metadata = dict(metadata)
    metadata_update: dict[str, Any] = {
        "source": _DASHBOARD_SOURCE,
        "updated_at_ms": now_ms,
        "plan_mode": body.plan_mode,
    }
    if chosen_model and chosen_effort:
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort
    if _is_thread_resolved(metadata):
        metadata_update["resolved"] = False
        metadata_update["resolved_at_ms"] = None

    active = await get_thread_active_status(thread_id)
    if active is None:
        raise HTTPException(502, "could not determine whether thread is active")
    if not active:
        raise HTTPException(
            409,
            "thread is idle; start a run via the stream commands endpoint",
        )

    active_model = _metadata_model_id(metadata) if body.images else None
    content = _user_message_content(prompt, body.images, model_id=active_model)
    await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    queue_payload: dict[str, Any] = {
        "text": prompt,
        "source": _DASHBOARD_SOURCE,
        "from_owner": _user_owns_thread(metadata, login, email),
    }
    if isinstance(content, list):
        queue_payload["images"] = [
            block for block in content if isinstance(block, dict) and block.get("type") != "text"
        ]
    queued = await queue_message_for_thread(thread_id, queue_payload)
    if not queued:
        raise HTTPException(502, "failed to queue follow-up message")
    try:
        await _notify_slack_web_handoff(thread_id, handoff_metadata, client)
    except Exception:
        logger.exception("Failed to update Slack message for dashboard handoff on %s", thread_id)
    thread = await client.threads.get(thread_id)
    return _thread_summary(thread)


async def cancel_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    _assert_thread_owner(metadata, login, email)

    run_id = metadata.get("latest_run_id")
    if isinstance(run_id, str) and run_id:
        try:
            await client.runs.cancel(thread_id, run_id, wait=False)
        except Exception:
            logger.debug("Could not cancel run %s for thread %s", run_id, thread_id, exc_info=True)

    await client.threads.update(
        thread_id=thread_id,
        metadata={"latest_run_status": "interrupted", "updated_at_ms": _now_ms()},
    )
    thread = await client.threads.get(thread_id)
    return _thread_summary(thread)


async def admin_cancel_dashboard_thread(thread_id: str) -> dict[str, Any]:
    client = langgraph_client()
    try:
        await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    try:
        await client.runs.cancel_many(thread_id=thread_id, status="all", action="interrupt")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to cancel active runs for thread %s", thread_id)
        raise HTTPException(502, "failed to request thread cancellation") from exc

    await client.threads.update(
        thread_id=thread_id,
        metadata={"latest_run_status": "interrupted", "updated_at_ms": _now_ms()},
    )
    updated_thread = await client.threads.get(thread_id)
    return _thread_summary(updated_thread)


async def delete_dashboard_thread(thread_id: str, login: str, *, email: str | None = None) -> None:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    _assert_thread_owner(metadata, login, email)

    run_id = metadata.get("latest_run_id")
    if isinstance(run_id, str) and run_id:
        try:
            await client.runs.cancel(thread_id, run_id, wait=False)
        except Exception:
            logger.debug("Could not cancel run %s for thread %s", run_id, thread_id, exc_info=True)

    await client.threads.delete(thread_id)


async def resolve_dashboard_thread(
    thread_id: str, login: str, *, resolved: bool, email: str | None = None
) -> dict[str, Any]:
    """Mark a thread resolved/unresolved via thread metadata."""
    client = langgraph_client()
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread_metadata(thread)
    metadata_update: dict[str, Any] = {
        "resolved": resolved,
        "resolved_at_ms": _now_ms() if resolved else None,
    }
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not update resolved state for thread %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to update thread") from exc
    thread = {**as_thread_dict(thread), "metadata": {**metadata, **metadata_update}}
    return _thread_summary(thread)


async def _authorized_thread_metadata(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread_metadata(thread)
    return metadata


async def _authorized_thread(thread_id: str, login: str, *, email: str | None = None) -> ThreadLike:
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    _assert_thread_owner(metadata, login, email)
    return thread


async def _readable_thread(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> ThreadLike:
    """Fetch a thread and assert it is readable by the requesting user.

    Read access is granted to any authenticated org member for surfaced-source
    threads; ``login``/``email`` are accepted for API parity but not required.
    """
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    _assert_thread_readable(metadata)
    return thread


async def _readable_thread_metadata(
    thread_id: str, *, login: str | None = None, email: str | None = None
) -> dict[str, Any]:
    thread = await _readable_thread(thread_id, login=login, email=email)
    metadata = thread_metadata(thread)
    return metadata


async def get_dashboard_thread_state(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    thread = await _readable_thread(thread_id, login=login, email=email)
    metadata = thread_metadata(thread)
    state = await langgraph_client().threads.get_state(thread_id)
    result = as_json_object(state)
    # The SDK's `useStream` opens its live event subscription only when the
    # hydrated `getState()` looks active (`next` non-empty / absent). When a
    # run was just started out-of-band (our REST run-create), the latest
    # checkpoint can still be the previous finished one with `next == []`,
    # which the SDK reads as idle and never opens the stream. Drop `next`
    # while a run is pending/running so the SDK treats the thread as active.
    metadata_run_status = metadata.get("latest_run_status")
    if _thread_is_busy(thread) or metadata_run_status in {"pending", "running"}:
        result.pop("next", None)
    return result


def _recovery_patch_filename(thread_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in {"-", "_", "."} else "-" for c in thread_id)
    return f"open-swe-{(safe or 'thread')[:80]}.patch"


def _response_output(result: Any) -> str:
    output = result.get("output") if isinstance(result, dict) else getattr(result, "output", "")
    return output if isinstance(output, str) else str(output or "")


def _response_exit_code(result: Any) -> int | None:
    value = (
        result.get("exit_code") if isinstance(result, dict) else getattr(result, "exit_code", None)
    )
    return value if isinstance(value, int) else None


def _download_content(result: Any) -> bytes | None:
    for attr in ("content", "data", "bytes"):
        value = result.get(attr) if isinstance(result, dict) else getattr(result, attr, None)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
    file_data = (
        result.get("file_data") if isinstance(result, dict) else getattr(result, "file_data", None)
    )
    if isinstance(file_data, bytes):
        return file_data
    if isinstance(file_data, str):
        return file_data.encode()
    if isinstance(file_data, dict):
        for key in ("content", "data", "bytes"):
            value = file_data.get(key)
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode()
    return None


def _recovery_patch_command(metadata: Mapping[str, Any], thread_id: str) -> str:
    _, name, _ = _metadata_repo(metadata)
    payload = {
        "repo_name": name,
        "base_branch": metadata.get("base_branch")
        if isinstance(metadata.get("base_branch"), str)
        else "main",
        "thread_key": _recovery_patch_filename(thread_id).removesuffix(".patch"),
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = r"""python - <<'PY'
import base64
import json
import subprocess
import sys
from pathlib import Path

PAYLOAD = json.loads(base64.b64decode('__PAYLOAD__').decode())
WORKSPACE_FALLBACK = Path('/workspace')


def git(repo, args, check=True):
    result = subprocess.run(
        ['git', '-C', str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode(errors='replace').strip()
        raise RuntimeError(detail or 'git ' + ' '.join(args) + ' failed')
    return result


def search_roots():
    roots = [Path.cwd().resolve(), WORKSPACE_FALLBACK]
    seen = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        if root.exists():
            yield root


def repo_paths():
    repo_name = PAYLOAD.get('repo_name')
    for root in search_roots():
        if isinstance(repo_name, str) and repo_name:
            yield root / Path(repo_name).name
        yield root
        for child in sorted(root.iterdir()):
            if child.is_dir():
                yield child


def find_repo():
    seen = set()
    for path in repo_paths():
        if path in seen:
            continue
        seen.add(path)
        if not (path / '.git').exists():
            continue
        result = git(path, ['rev-parse', '--show-toplevel'], check=False)
        if result.returncode == 0:
            root = Path(result.stdout.decode(errors='replace').strip())
            if root.exists():
                return root
    raise RuntimeError('no git repository found in sandbox workspace')


def safe_ref(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if value.startswith('-') or '\x00' in value or '\n' in value or '\r' in value:
        return None
    return value


def commit_for(repo, ref):
    result = git(repo, ['rev-parse', '--verify', ref + '^{commit}'], check=False)
    if result.returncode == 0:
        return result.stdout.decode(errors='replace').strip()
    return None


def merge_base(repo):
    base_branch = safe_ref(PAYLOAD.get('base_branch')) or 'main'
    refs = ['origin/' + base_branch, base_branch, 'origin/main', 'main', 'origin/master', 'master', 'HEAD~1']
    for ref in refs:
        commit = commit_for(repo, ref)
        if not commit:
            continue
        result = git(repo, ['merge-base', 'HEAD', commit], check=False)
        if result.returncode == 0:
            return result.stdout.decode(errors='replace').strip()
        return commit
    return git(repo, ['hash-object', '-t', 'tree', '/dev/null']).stdout.decode(errors='replace').strip()


def write_patch(repo, base):
    patch_path = Path('/tmp') / ((PAYLOAD.get('thread_key') or 'open-swe-recovery') + '.patch')
    with patch_path.open('wb') as patch_file:
        tracked = git(repo, ['diff', '--binary', '--full-index', base, '--', '.']).stdout
        patch_file.write(tracked)
        untracked = git(repo, ['ls-files', '--others', '--exclude-standard', '-z']).stdout
        for raw_path in [p for p in untracked.split(b'\0') if p]:
            rel_path = raw_path.decode('utf-8', errors='surrogateescape')
            full_path = repo / rel_path
            if not full_path.is_file():
                continue
            result = git(
                repo,
                ['diff', '--no-index', '--binary', '--full-index', '--', '/dev/null', rel_path],
                check=False,
            )
            if result.returncode not in {0, 1}:
                detail = result.stderr.decode(errors='replace').strip()
                raise RuntimeError(detail or 'failed to diff untracked file ' + rel_path)
            if result.stdout:
                if patch_file.tell() and not result.stdout.startswith(b'\n'):
                    patch_file.write(b'\n')
                patch_file.write(result.stdout)
    return patch_path


try:
    repo = find_repo()
    base = merge_base(repo)
    patch_path = write_patch(repo, base)
    print(json.dumps({'ok': True, 'path': str(patch_path), 'size': patch_path.stat().st_size}))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}))
    sys.exit(1)
PY"""
    return script.replace("__PAYLOAD__", encoded)


async def get_dashboard_thread_recovery_patch(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[bytes, str]:
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread_metadata(thread)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise HTTPException(404, "thread has no recoverable sandbox")

    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not connect to sandbox %s for recovery", sandbox_id, exc_info=True)
        raise HTTPException(502, "could not connect to thread sandbox") from exc

    try:
        result = await asyncio.to_thread(
            sandbox.execute,
            _recovery_patch_command(metadata, thread_id),
            timeout=_RECOVERY_PATCH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch generation failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    output = _response_output(result).strip()
    try:
        payload = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        logger.debug("Invalid recovery patch response for %s: %s", thread_id, output)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    if _response_exit_code(result) not in {0, None} or payload.get("ok") is not True:
        detail = payload.get("error") if isinstance(payload.get("error"), str) else None
        logger.debug("Recovery patch generation failed for %s: %s", thread_id, detail)
        raise HTTPException(502, detail or "failed to generate recovery patch")

    size = payload.get("size")
    if not isinstance(size, int):
        raise HTTPException(502, "failed to generate recovery patch")
    if size == 0:
        raise HTTPException(404, "thread has no recoverable changes")
    if size > _RECOVERY_PATCH_LIMIT_BYTES:
        raise HTTPException(413, "recovery patch is too large to download")

    patch_path = payload.get("path")
    if not isinstance(patch_path, str) or not patch_path.startswith("/tmp/"):
        raise HTTPException(502, "failed to generate recovery patch")

    try:
        downloads = await asyncio.to_thread(sandbox.download_files, [patch_path])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch download failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to download recovery patch") from exc
    if not downloads:
        raise HTTPException(502, "failed to download recovery patch")
    content = _download_content(downloads[0])
    if content is None:
        raise HTTPException(502, "failed to download recovery patch")
    return content, _recovery_patch_filename(thread_id)


# No app-installation-token fallback: PR file contents must be fetched with
# the user's own credential so GitHub enforces their current repo access.
async def _github_token_for_login(login: str) -> str:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    return token


async def get_dashboard_thread_pr_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    pr_number = metadata.get("pr_number")
    _, _, full_name = _metadata_repo(metadata)
    if not isinstance(pr_number, int) or not full_name:
        raise HTTPException(404, "thread has no pull request")

    token = await _github_token_for_login(login)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(headers=headers, timeout=_PROXY_REQUEST_TIMEOUT) as client:
        diff = await build_pr_diff_files(client, full_name, pr_number)

    return {
        "prNumber": pr_number,
        "baseSha": diff["base_sha"],
        "headSha": diff["head_sha"],
        "truncated": diff["truncated"],
        "files": diff["files"],
    }


async def proxy_dashboard_thread_stream_events(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> AsyncIterator[bytes]:
    # Preflight here (not in the generator) so auth/content-type failures
    # surface as real HTTP errors before the SSE response starts streaming.
    _require_json_content_type(content_type)
    await _readable_thread_metadata(thread_id, login=login, email=email)
    return _stream_thread_events(thread_id, body, content_type)


async def _stream_thread_events(
    thread_id: str,
    body: bytes,
    content_type: str,
) -> AsyncIterator[bytes]:
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/stream/events"
    headers = _langgraph_proxy_headers(content_type=content_type, accept="text/event-stream")

    try:
        async with httpx.AsyncClient(timeout=_PROXY_STREAM_TIMEOUT) as client:
            async with client.stream("POST", url, content=body, headers=headers) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    payload = {
                        "status": response.status_code,
                        "detail": error_body.decode(errors="replace") or response.reason_phrase,
                    }
                    yield f"event: error\ndata: {json.dumps(payload)}\n\n".encode()
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
    except Exception:
        logger.warning("LangGraph stream/events proxy closed for %s", thread_id, exc_info=True)


async def proxy_dashboard_thread_commands(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    _require_json_content_type(content_type)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "command body must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "command body must be a JSON object")

    # The dashboard mints the thread id client-side and submits straight away,
    # so the very first ``run.start`` may target a thread that doesn't exist
    # yet. That command lazily creates + stamps + owns the thread (in
    # ``_enrich_run_start_command``); any other command against a missing thread
    # is a 404. On an existing thread, ``run.start`` (the posting path) is open
    # to any org member and attributed in ``_enrich_run_start_command``; every
    # other write command carries unattributed input (e.g. ``input.respond``),
    # so it stays owner-only.
    method = parsed.get("method")
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception:  # noqa: BLE001
        thread = None

    creating = False
    if thread is None:
        if method != "run.start":
            raise HTTPException(404, "thread not found")
        creating = True
        metadata: dict[str, Any] = {}
        thread_busy = False
    else:
        metadata = thread_metadata(thread)
        if method == "run.start":
            _assert_thread_readable(metadata)
        else:
            _assert_thread_owner(metadata, login, email)
        metadata_run_status = metadata.get("latest_run_status")
        thread_busy = _thread_is_busy(thread) or metadata_run_status in {"pending", "running"}

    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/commands"
    headers = _langgraph_proxy_headers(content_type=content_type)

    enriched = await _enrich_run_start_command(
        thread_id,
        login,
        parsed,
        metadata=metadata,
        thread_busy=thread_busy,
        creating=creating,
        email=email,
    )
    outgoing = json.dumps(enriched).encode()

    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, content=outgoing, headers=headers)

    run_start_succeeded = parsed.get("method") == "run.start" and response.status_code in {
        200,
        202,
        204,
    }
    if run_start_succeeded and not creating:
        try:
            await _notify_slack_web_handoff(thread_id, metadata, langgraph_client())
        except Exception:
            logger.exception(
                "Failed to update Slack message for dashboard handoff on %s", thread_id
            )

    if run_start_succeeded and response.content:
        try:
            payload = json.loads(response.content)
        except json.JSONDecodeError:
            payload = None
        run_id = _extract_run_id_from_command_response(payload)
        if run_id:
            await langgraph_client().threads.update(
                thread_id=thread_id,
                metadata={
                    "latest_run_id": run_id,
                    "latest_run_status": "pending",
                    "updated_at_ms": _now_ms(),
                },
            )

    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def proxy_dashboard_thread_history(
    thread_id: str,
    login: str,
    body: bytes,
    *,
    email: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    _require_json_content_type(content_type)
    await _readable_thread_metadata(thread_id, login=login, email=email)
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/history"
    headers = _langgraph_proxy_headers(content_type=content_type)
    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, content=body or b"{}", headers=headers)
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def proxy_dashboard_thread_run_cancel(
    thread_id: str,
    run_id: str,
    login: str,
    *,
    wait: str = "0",
    action: str = "interrupt",
    email: str | None = None,
) -> tuple[int, bytes, str | None]:
    await _authorized_thread_metadata(thread_id, login, email=email)
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/runs/{run_id}/cancel"
    headers = _langgraph_proxy_headers()
    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(
            url,
            headers=headers,
            params={"wait": wait, "action": action},
        )
    if response.status_code in {200, 202, 204}:
        try:
            await langgraph_client().threads.update(
                thread_id=thread_id,
                metadata={
                    "latest_run_status": "interrupted",
                    "updated_at_ms": _now_ms(),
                },
            )
        except Exception:
            logger.debug(
                "Could not update thread metadata after run cancel for %s",
                thread_id,
                exc_info=True,
            )
    media_type = response.headers.get("content-type")
    return response.status_code, response.content, media_type


async def stream_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None, last_event_id: str | None = None
) -> AsyncIterator[str]:
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    _assert_thread_readable(metadata)

    stream = await langgraph_client().threads.join_stream(
        thread_id,
        last_event_id=last_event_id,
    )
    async for part in stream:
        event = getattr(part, "event", None) or (
            part.get("event") if isinstance(part, dict) else None
        )
        data = getattr(part, "data", None) if not isinstance(part, dict) else part.get("data")
        event_id = getattr(part, "id", None) if not isinstance(part, dict) else part.get("id")
        payload: dict[str, Any] = {"event": event, "data": data}
        if event_id is not None:
            payload["id"] = event_id
        chunk = f"data: {json.dumps(payload, default=str)}\n\n"
        if event_id is not None:
            chunk = f"id: {event_id}\n{chunk}"
        yield chunk
