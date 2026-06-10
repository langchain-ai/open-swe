"""Dashboard thread list/detail/run/stream endpoints backed by LangGraph."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from langchain_core.messages.content import create_image_block
from langgraph_sdk.errors import InternalServerError
from pydantic import BaseModel, ConfigDict, Field

from ..utils.thread_ops import is_thread_active, langgraph_client, queue_message_for_thread
from .agent_overrides import normalize_profile_overrides
from .message_adapter import state_messages_to_ui
from .options import SUPPORTED_MODEL_IDS, model_supports_effort, model_supports_images
from .profiles import get_profile, get_valid_access_token
from .team_settings import get_team_default_model
from .user_mappings import email_for_login

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
_DASHBOARD_SOURCE = "dashboard"
_DASHBOARD_STREAM_MODES: tuple[str, ...] = ("values", "updates", "messages-tuple")
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_MAX_DASHBOARD_IMAGES = 5
_MAX_DASHBOARD_IMAGE_BYTES = 10 * 1024 * 1024
# Sources whose threads should surface in the Agents UI (besides "dashboard").
_SURFACED_SOURCES: tuple[str, ...] = ("dashboard", "github", "slack", "linear", "schedule")


def _agent_version_metadata() -> dict[str, str]:
    revision = os.environ.get("LANGCHAIN_REVISION_ID")
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


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


class ThreadCreateBody(BaseModel):
    prompt: str = Field(default="", max_length=20_000)
    images: list[DashboardImageBody] = Field(default_factory=list)
    repo: str | None = None
    repo_explicitly_none: bool = False
    model_id: str | None = None
    effort: str | None = None


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
    messages: list[dict[str, Any]] | None = None,
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

    summary: dict[str, Any] = {
        "id": thread.get("thread_id") or thread.get("id"),
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
    }
    if isinstance(pr_number, int) and isinstance(pr_url, str):
        summary["pr"] = {
            "number": pr_number,
            "title": pr_title if isinstance(pr_title, str) else title,
            "state": pr_state if isinstance(pr_state, str) else "open",
            "headRef": metadata.get("branch_name") or "",
            "baseRef": metadata.get("base_branch") or "main",
            "url": pr_url,
        }
    if messages is not None:
        summary["messages"] = messages
    else:
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

    messages: list[dict[str, Any]] = []
    try:
        state = await client.threads.get_state(thread_id)
    except InternalServerError:
        logger.warning(
            "Thread state unavailable for %s (checkpoint replay failed); returning metadata only",
            thread_id,
        )
    else:
        values = state.get("values") if isinstance(state, dict) else {}
        raw_messages = values.get("messages") if isinstance(values, dict) else []
        messages = state_messages_to_ui(raw_messages if isinstance(raw_messages, list) else [])

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
        messages=messages,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
    )


def _resolve_repo_config(repo: str | None) -> dict[str, str]:
    """Resolve the run's repo from the request, or ``{}`` when none is given."""
    return _parse_repo(repo) or {}


async def _start_agent_run(
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
    profile = await get_profile(login) or {}
    now_ms = _now_ms()
    prompt = prompt.strip()
    resolved_model, resolved_effort = await _resolve_agent_model_choice(profile, model_id, effort)
    content = _user_message_content(prompt, images or [], model_id=resolved_model)
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
    await _ensure_dashboard_github_token(login)

    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": _DASHBOARD_SOURCE,
        "github_login": login,
        "user_email": await _resolve_run_email(login, profile),
    }
    if has_repo:
        configurable["repo"] = repo_config
    elif repo_explicitly_none:
        configurable["repo_explicitly_none"] = True
    if chosen_model and chosen_effort:
        configurable["agent_model_id"] = chosen_model
        configurable["agent_effort"] = chosen_effort

    run = await client.runs.create(
        thread_id,
        _ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": content}]},
        config={"configurable": configurable, "metadata": _agent_version_metadata()},
        if_not_exists="create",
        stream_mode=list(_DASHBOARD_STREAM_MODES),
        stream_resumable=True,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    await client.threads.update(
        thread_id=thread_id,
        metadata={"latest_run_id": run_id, "latest_run_status": "pending", "updated_at_ms": now_ms},
    )
    thread = await client.threads.get(thread_id)
    return _thread_summary(
        thread if isinstance(thread, dict) else {"thread_id": thread_id, "metadata": metadata}
    )


async def create_dashboard_thread(login: str, body: ThreadCreateBody) -> dict[str, Any]:
    repo_config = _resolve_repo_config(body.repo)
    thread_id = str(uuid.uuid4())
    return await _start_agent_run(
        thread_id,
        login=login,
        repo_config=repo_config,
        repo_explicitly_none=body.repo_explicitly_none,
        prompt=body.prompt,
        images=body.images,
        model_id=body.model_id,
        effort=body.effort,
    )


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
    owner, name, _ = _metadata_repo(metadata)

    prompt = body.content.strip()
    now_ms = _now_ms()
    chosen_model, chosen_effort = _normalize_model_choice(body.model_id, body.effort)
    metadata_update: dict[str, Any] = {"source": _DASHBOARD_SOURCE, "updated_at_ms": now_ms}
    if chosen_model and chosen_effort:
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort

    if await is_thread_active(thread_id):
        active_model = _metadata_model_id(metadata) if body.images else None
        content = _user_message_content(prompt, body.images, model_id=active_model)
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
        queue_payload: dict[str, Any] = {"text": prompt, "source": _DASHBOARD_SOURCE}
        if isinstance(content, list):
            queue_payload["images"] = [
                block
                for block in content
                if isinstance(block, dict) and block.get("type") != "text"
            ]
        queued = await queue_message_for_thread(
            thread_id,
            queue_payload,
        )
        if not queued:
            raise HTTPException(502, "failed to queue follow-up message")
        thread = await client.threads.get(thread_id)
        return _thread_summary(
            thread if isinstance(thread, dict) else {"thread_id": thread_id, "metadata": metadata}
        )

    profile = await get_profile(login) or {}
    resolved_model, resolved_effort = await _resolve_agent_model_choice(
        profile, body.model_id, body.effort
    )
    metadata_update["resolved_model"] = resolved_model
    metadata_update["resolved_effort"] = resolved_effort
    content = _user_message_content(prompt, body.images, model_id=resolved_model)
    await client.threads.update(thread_id=thread_id, metadata=metadata_update)

    await _ensure_dashboard_github_token(login)
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": _DASHBOARD_SOURCE,
        "github_login": login,
        "user_email": await _resolve_run_email(login, profile),
    }
    if owner and name:
        configurable["repo"] = {"owner": owner, "name": name}
    elif metadata.get("repo_explicitly_none") is True:
        configurable["repo_explicitly_none"] = True
    if chosen_model and chosen_effort:
        configurable["agent_model_id"] = chosen_model
        configurable["agent_effort"] = chosen_effort
    run = await client.runs.create(
        thread_id,
        _ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": content}]},
        config={"configurable": configurable, "metadata": _agent_version_metadata()},
        stream_mode=list(_DASHBOARD_STREAM_MODES),
        stream_resumable=True,
    )
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
    await client.threads.update(
        thread_id=thread_id,
        metadata={"latest_run_id": run_id, "latest_run_status": "pending", "updated_at_ms": now_ms},
    )
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
