"""Dashboard thread list/detail/run/stream endpoints backed by LangGraph."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException
from langchain_core.messages.content import create_image_block
from pydantic import BaseModel, ConfigDict, Field

from ..utils.langsmith import get_langsmith_trace_url
from ..utils.thread_ops import (
    get_thread_active_status,
    langgraph_client,
    langgraph_url,
    queue_message_for_thread,
)
from .agent_overrides import normalize_profile_overrides
from .options import SUPPORTED_MODEL_IDS, model_supports_effort, model_supports_images
from .profiles import get_profile, get_valid_access_token
from .team_settings import get_team_default_model
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


def _thread_is_busy(thread: dict[str, Any]) -> bool:
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
    return resolved_model, resolved_effort


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
) -> list[dict[str, Any]]:
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
) -> str | list[dict[str, Any]]:
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


def _thread_owner_login(metadata: dict[str, Any]) -> str | None:
    login = metadata.get("github_login")
    return login.strip() if isinstance(login, str) and login.strip() else None


def _thread_owner_email(metadata: dict[str, Any]) -> str | None:
    email = metadata.get("triggering_user_email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def _thread_source(metadata: dict[str, Any]) -> str:
    source = metadata.get("source")
    return source if isinstance(source, str) and source else _DASHBOARD_SOURCE


def _metadata_model_id(metadata: dict[str, Any]) -> str | None:
    for key in ("resolved_model", "model"):
        model = metadata.get(key)
        if isinstance(model, str) and model in SUPPORTED_MODEL_IDS:
            return model
    return None


def _user_owns_thread(metadata: dict[str, Any], login: str, email: str | None) -> bool:
    if _thread_source(metadata) not in _SURFACED_SOURCES:
        return False
    if _thread_owner_login(metadata) == login:
        return True
    if email and _thread_owner_email(metadata) == email.strip().lower():
        return True
    return False


def _assert_thread_owner(metadata: dict[str, Any], login: str, email: str | None = None) -> None:
    if not _user_owns_thread(metadata, login, email):
        raise HTTPException(404, "thread not found")


def _metadata_repo(metadata: dict[str, Any]) -> tuple[str, str, str]:
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


def _thread_run_id(metadata: dict[str, Any], latest_run_id: str | None) -> str | None:
    if latest_run_id:
        return latest_run_id
    run_id = metadata.get("latest_run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def _is_thread_viewed(metadata: dict[str, Any], latest_run_id: str | None) -> bool:
    viewed_at = metadata.get("last_viewed_at_ms")
    viewed_run_id = metadata.get("last_viewed_run_id")
    run_id = _thread_run_id(metadata, latest_run_id)
    if run_id:
        return viewed_run_id == run_id
    return isinstance(viewed_at, (int, float))


def _thread_summary(
    thread: dict[str, Any],
    *,
    latest_run_status: str | None = None,
    latest_run_id: str | None = None,
) -> dict[str, Any]:
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
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

    summary: dict[str, Any] = {
        "id": thread_id,
        "title": title,
        "repo": name,
        "repoFullName": full_name,
        "branch": metadata.get("branch_name") or metadata.get("base_branch") or "main",
        "model": model,
        "effort": effort,
        "source": _thread_source(metadata),
        "status": status,
        "viewed": _is_thread_viewed(metadata, latest_run_id),
        "viewedAt": (
            int(metadata["last_viewed_at_ms"])
            if isinstance(metadata.get("last_viewed_at_ms"), (int, float))
            else None
        ),
        "createdAt": int(created_at) if isinstance(created_at, (int, float)) else _now_ms(),
        "updatedAt": int(updated_at) if isinstance(updated_at, (int, float)) else _now_ms(),
        "traceUrl": trace_url,
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
    diff_stats = metadata.get("diff_stats")
    if isinstance(diff_stats, dict):
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
    client: Any, thread: dict[str, Any]
) -> tuple[dict[str, Any], str | None, str | None]:
    thread_id = thread.get("thread_id") or thread.get("id")
    if not isinstance(thread_id, str) or not thread_id:
        return thread, None, None
    latest_run_status, latest_run_id = await _latest_run_info(client, thread_id)
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
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
            thread = {**thread, "metadata": {**metadata, **metadata_update}}
    return thread, latest_run_status, latest_run_id


async def list_dashboard_threads(
    login: str, *, email: str | None = None, limit: int = 50, include_all: bool = False
) -> list[dict[str, Any]]:
    client = langgraph_client()
    searches: list[dict[str, Any]] = [{}] if include_all else [{"github_login": login}]
    if not include_all and email and email.strip():
        searches.append({"triggering_user_email": email.strip().lower()})

    seen: dict[str, dict[str, Any]] = {}
    for metadata_filter in searches:
        threads = await client.threads.search(
            metadata=metadata_filter,
            limit=limit,
            sort_by="updated_at",
            sort_order="desc",
        )
        for thread in threads or []:
            if not isinstance(thread, dict):
                continue
            meta = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
            if not include_all and not _user_owns_thread(meta, login, email):
                continue
            thread_id = thread.get("thread_id") or thread.get("id")
            if isinstance(thread_id, str) and thread_id not in seen:
                seen[thread_id] = thread

    summaries: list[dict[str, Any]] = []
    for thread in seen.values():
        refreshed, latest_run_status, latest_run_id = await _refresh_latest_run_metadata(
            client, thread
        )
        summaries.append(
            _thread_summary(
                refreshed,
                latest_run_status=latest_run_status,
                latest_run_id=latest_run_id,
            )
        )
    summaries.sort(key=lambda item: item.get("updatedAt", 0), reverse=True)
    return summaries[:limit]


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

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    is_owner = _user_owns_thread(metadata, login, email)

    # The transcript is hydrated client-side by the SDK (`StreamProvider` reads
    # `GET …/state` → `stream.messages`), so the detail endpoint returns
    # metadata only — no server-side message conversion.
    thread, latest_run_status, latest_run_id = await _refresh_latest_run_metadata(client, thread)
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else metadata
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
        thread = {**thread, "metadata": metadata}

    return _thread_summary(
        thread,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
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
) -> dict[str, Any]:
    """Create or update dashboard thread metadata without starting a run."""
    profile = await get_profile(login) or {}
    now_ms = _now_ms()
    prompt = prompt.strip()
    resolved_model, resolved_effort = await _resolve_agent_model_choice(profile, model_id, effort)
    # Validate any attached images against the resolved model (raises 422 for
    # text-only models). The run itself is started client-side via the stream
    # commands endpoint, so we only need the validation side effect here.
    _user_message_content(prompt, images or [], model_id=resolved_model)
    chosen_model, chosen_effort = _normalize_model_choice(model_id, effort)
    metadata_model = chosen_model or profile.get("default_model") or "Default"
    metadata_effort = chosen_effort or profile.get("reasoning_effort")
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
    return thread if isinstance(thread, dict) else {"thread_id": thread_id, "metadata": metadata}


def _repo_config_from_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    owner, name, _ = _metadata_repo(metadata)
    if owner and name:
        return {"owner": owner, "name": name}
    return {}


async def _build_dashboard_configurable(
    thread_id: str,
    login: str,
    metadata: dict[str, Any],
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
                mime_type=mime,
                file_name=file_name if isinstance(file_name, str) else None,
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
    content = _command_message_content(params)
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
            images=_dashboard_images_from_content(content),
            model_id=client_configurable.get("agent_model_id"),
            effort=client_configurable.get("agent_effort"),
        )
        metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else metadata
        if chosen_model and chosen_effort:
            overrides["agent_model_id"] = chosen_model
            overrides["agent_effort"] = chosen_effort
    else:
        _validate_command_images(content, model_id=chosen_model or _metadata_model_id(metadata))
        if chosen_model and chosen_effort:
            overrides["agent_model_id"] = chosen_model
            overrides["agent_effort"] = chosen_effort
            metadata = {
                **metadata,
                "model": chosen_model,
                "effort": chosen_effort,
                "updated_at_ms": _now_ms(),
            }
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


async def send_dashboard_message(
    thread_id: str, login: str, body: ThreadMessageBody, *, email: str | None = None
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login, email)

    prompt = body.content.strip()
    now_ms = _now_ms()
    chosen_model, chosen_effort = _normalize_model_choice(body.model_id, body.effort)
    metadata_update: dict[str, Any] = {"source": _DASHBOARD_SOURCE, "updated_at_ms": now_ms}
    if chosen_model and chosen_effort:
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort

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
    queue_payload: dict[str, Any] = {"text": prompt, "source": _DASHBOARD_SOURCE}
    if isinstance(content, list):
        queue_payload["images"] = [
            block for block in content if isinstance(block, dict) and block.get("type") != "text"
        ]
    queued = await queue_message_for_thread(thread_id, queue_payload)
    if not queued:
        raise HTTPException(502, "failed to queue follow-up message")
    thread = await client.threads.get(thread_id)
    return _thread_summary(
        thread if isinstance(thread, dict) else {"thread_id": thread_id, "metadata": metadata}
    )


async def cancel_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
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
    return _thread_summary(
        thread if isinstance(thread, dict) else {"thread_id": thread_id, "metadata": metadata}
    )


async def delete_dashboard_thread(thread_id: str, login: str, *, email: str | None = None) -> None:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login, email)

    run_id = metadata.get("latest_run_id")
    if isinstance(run_id, str) and run_id:
        try:
            await client.runs.cancel(thread_id, run_id, wait=False)
        except Exception:
            logger.debug("Could not cancel run %s for thread %s", run_id, thread_id, exc_info=True)

    await client.threads.delete(thread_id)


async def _authorized_thread_metadata(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    return metadata


async def _authorized_thread(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login, email)
    return thread


async def get_dashboard_thread_state(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    state = await langgraph_client().threads.get_state(thread_id)
    result = state if isinstance(state, dict) else dict(state)
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


_PR_DIFF_MAX_FILES = 50
_PR_DIFF_MAX_FILE_BYTES = 200_000
_PR_DIFF_FETCH_CONCURRENCY = 5
_GITHUB_API = "https://api.github.com"


# No app-installation-token fallback: PR file contents must be fetched with
# the user's own credential so GitHub enforces their current repo access.
async def _github_token_for_login(login: str) -> str:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    return token


async def _fetch_file_at_ref(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    full_name: str,
    path: str,
    ref: str,
) -> str | None:
    async with semaphore:
        response = await client.get(
            f"{_GITHUB_API}/repos/{full_name}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
    if response.status_code == 404:
        return ""
    if response.status_code != 200:
        return None
    if len(response.content) > _PR_DIFF_MAX_FILE_BYTES:
        return None
    try:
        return response.content.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def get_dashboard_thread_pr_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    metadata = await _authorized_thread_metadata(thread_id, login, email=email)
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
        pull_response = await client.get(f"{_GITHUB_API}/repos/{full_name}/pulls/{pr_number}")
        if pull_response.status_code == 404:
            raise HTTPException(404, "pull request not found")
        if pull_response.status_code != 200:
            raise HTTPException(502, f"github API error ({pull_response.status_code})")
        pull = pull_response.json()
        base_sha = pull.get("base", {}).get("sha")
        head_sha = pull.get("head", {}).get("sha")
        if not isinstance(base_sha, str) or not isinstance(head_sha, str):
            raise HTTPException(502, "github API returned an unexpected pull request payload")

        files_response = await client.get(
            f"{_GITHUB_API}/repos/{full_name}/pulls/{pr_number}/files",
            params={"per_page": 100},
        )
        if files_response.status_code != 200:
            raise HTTPException(502, f"github API error ({files_response.status_code})")
        raw_files = files_response.json()
        if not isinstance(raw_files, list):
            raise HTTPException(502, "github API returned an unexpected files payload")

        truncated = len(raw_files) > _PR_DIFF_MAX_FILES
        raw_files = raw_files[:_PR_DIFF_MAX_FILES]

        semaphore = asyncio.Semaphore(_PR_DIFF_FETCH_CONCURRENCY)

        async def build_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
            path = raw.get("filename")
            if not isinstance(path, str):
                return None
            status = raw.get("status") if isinstance(raw.get("status"), str) else "modified"
            previous = raw.get("previous_filename")
            original_path = previous if isinstance(previous, str) else path

            original: str | None = ""
            modified: str | None = ""
            if status != "added":
                original = await _fetch_file_at_ref(
                    client, semaphore, full_name, original_path, base_sha
                )
            if status != "removed":
                modified = await _fetch_file_at_ref(client, semaphore, full_name, path, head_sha)

            return {
                "path": path,
                "previousPath": previous if isinstance(previous, str) else None,
                "status": status,
                "additions": raw.get("additions") if isinstance(raw.get("additions"), int) else 0,
                "deletions": raw.get("deletions") if isinstance(raw.get("deletions"), int) else 0,
                "originalContent": original,
                "modifiedContent": modified,
                # Binary or oversized blobs come back as None — the client
                # renders a placeholder instead of file contents.
                "unrenderable": original is None or modified is None,
            }

        entries = await asyncio.gather(*(build_entry(raw) for raw in raw_files))

    return {
        "prNumber": pr_number,
        "baseSha": base_sha,
        "headSha": head_sha,
        "truncated": truncated,
        "files": [entry for entry in entries if entry is not None],
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
    await _authorized_thread_metadata(thread_id, login, email=email)
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
    # ``_enrich_run_start_command``); any other command against a missing
    # thread — or a command from a non-owner against an existing thread — is a
    # 404.
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
        metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
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
    )
    outgoing = json.dumps(enriched).encode()

    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, content=outgoing, headers=headers)

    if (
        parsed.get("method") == "run.start"
        and response.status_code in {200, 202, 204}
        and response.content
    ):
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
    await _authorized_thread_metadata(thread_id, login, email=email)
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
        await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

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
