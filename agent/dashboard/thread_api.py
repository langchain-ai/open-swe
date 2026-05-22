"""Dashboard thread list/detail/run/stream endpoints backed by LangGraph."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from langgraph_sdk.errors import InternalServerError
from pydantic import BaseModel, Field

from ..utils.auth import persist_encrypted_github_token
from ..utils.thread_ops import is_thread_active, langgraph_client, queue_message_for_thread
from .agent_overrides import get_profile_default_repo
from .message_adapter import state_messages_to_ui
from .options import SUPPORTED_MODEL_IDS, model_supports_effort
from .profiles import OAUTH_TOKENS_NAMESPACE, get_profile, get_valid_access_token
from .profiles import _get_value as get_oauth_record

logger = logging.getLogger(__name__)

_ASSISTANT_ID = "agent"
_DASHBOARD_SOURCE = "dashboard"


def _agent_version_metadata() -> dict[str, str]:
    revision = os.environ.get("LANGCHAIN_REVISION_ID")
    return {"LANGSMITH_AGENT_VERSION": revision} if revision else {}


class ThreadCreateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None


class ThreadMessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
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


async def _persist_dashboard_github_token(thread_id: str, login: str) -> None:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    record = await get_oauth_record(OAUTH_TOKENS_NAMESPACE, login)
    expires_at = record.get("token_expires_at") if isinstance(record, dict) else None
    await persist_encrypted_github_token(
        thread_id,
        token,
        expires_at=expires_at if isinstance(expires_at, str) else None,
    )


def _thread_owner_login(metadata: dict[str, Any]) -> str | None:
    login = metadata.get("github_login")
    return login.strip() if isinstance(login, str) and login.strip() else None


def _assert_thread_owner(metadata: dict[str, Any], login: str) -> None:
    owner = _thread_owner_login(metadata)
    if owner != login:
        raise HTTPException(404, "thread not found")
    if metadata.get("source") != _DASHBOARD_SOURCE:
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


def _thread_summary(
    thread: dict[str, Any], *, messages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    owner, name, full_name = _metadata_repo(metadata)
    created_at = metadata.get("created_at_ms")
    updated_at = metadata.get("updated_at_ms")
    title = metadata.get("title") if isinstance(metadata.get("title"), str) else "Untitled agent"
    model = metadata.get("model") if isinstance(metadata.get("model"), str) else "Default"
    effort = metadata.get("effort") if isinstance(metadata.get("effort"), str) else None
    thread_status = thread.get("status") if isinstance(thread.get("status"), str) else "idle"
    latest_run_status = metadata.get("latest_run_status")
    status = _run_status_to_agent_status(
        thread_status,
        latest_run_status if isinstance(latest_run_status, str) else None,
    )

    pr_number = metadata.get("pr_number")
    pr_url = metadata.get("pr_url")
    pr_title = metadata.get("pr_title")
    pr_state = metadata.get("pr_state")

    summary: dict[str, Any] = {
        "id": thread.get("thread_id") or thread.get("id"),
        "title": title,
        "repo": name or "unknown",
        "repoFullName": full_name or "unknown/unknown",
        "branch": metadata.get("branch_name") or metadata.get("base_branch") or "main",
        "model": model,
        "effort": effort,
        "status": status,
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


async def _latest_run_status(thread_id: str) -> str | None:
    runs = await langgraph_client().runs.list(thread_id, limit=1)
    if not runs:
        return None
    run = runs[0]
    raw = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
    return raw.lower() if isinstance(raw, str) else None


async def list_dashboard_threads(login: str, *, limit: int = 50) -> list[dict[str, Any]]:
    threads = await langgraph_client().threads.search(
        metadata={"source": _DASHBOARD_SOURCE, "github_login": login},
        limit=limit,
        sort_by="updated_at",
        sort_order="desc",
    )
    out: list[dict[str, Any]] = []
    for thread in threads or []:
        if isinstance(thread, dict):
            out.append(_thread_summary(thread))
    return out


async def get_dashboard_thread(thread_id: str, login: str) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Thread lookup failed for %s", thread_id, exc_info=True)
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login)

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

    latest_run_status = await _latest_run_status(thread_id)
    if latest_run_status and latest_run_status != metadata.get("latest_run_status"):
        metadata = {**metadata, "latest_run_status": latest_run_status}
        thread = {**thread, "metadata": metadata}

    return _thread_summary(thread, messages=messages)


async def _resolve_repo_config(login: str, repo: str | None) -> dict[str, str]:
    parsed = _parse_repo(repo)
    if parsed:
        return parsed
    profile_repo = await get_profile_default_repo(login)
    if profile_repo:
        return profile_repo
    profile = await get_profile(login)
    parsed = _parse_repo(profile.get("default_repo") if isinstance(profile, dict) else None)
    if parsed:
        return parsed
    raise HTTPException(400, "no default repository configured — set one in Cloud Agents settings")


async def _start_agent_run(
    thread_id: str,
    *,
    login: str,
    repo_config: dict[str, str],
    prompt: str,
    title: str | None = None,
    model_id: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    profile = await get_profile(login) or {}
    now_ms = _now_ms()
    chosen_model, chosen_effort = _normalize_model_choice(model_id, effort)
    metadata_model = chosen_model or profile.get("default_model") or "Default"
    metadata_effort = chosen_effort or profile.get("reasoning_effort")
    metadata = {
        "source": _DASHBOARD_SOURCE,
        "github_login": login,
        "title": title or prompt[:80] or "New agent",
        "repo_owner": repo_config["owner"],
        "repo_name": repo_config["name"],
        "base_branch": profile.get("base_branch") or "main",
        "branch_prefix": profile.get("branch_prefix"),
        "model": metadata_model,
        "effort": metadata_effort,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }

    client = langgraph_client()
    await client.threads.create(thread_id=thread_id, metadata=metadata, if_exists="do_nothing")
    await client.threads.update(thread_id=thread_id, metadata=metadata)
    await _persist_dashboard_github_token(thread_id, login)

    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": _DASHBOARD_SOURCE,
        "github_login": login,
        "repo": repo_config,
        "user_email": profile.get("email"),
    }
    if chosen_model and chosen_effort:
        configurable["agent_model_id"] = chosen_model
        configurable["agent_effort"] = chosen_effort

    run = await client.runs.create(
        thread_id,
        _ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable, "metadata": _agent_version_metadata()},
        if_not_exists="create",
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
    repo_config = await _resolve_repo_config(login, body.repo)
    thread_id = str(uuid.uuid4())
    return await _start_agent_run(
        thread_id,
        login=login,
        repo_config=repo_config,
        prompt=body.prompt.strip(),
        model_id=body.model_id,
        effort=body.effort,
    )


async def send_dashboard_message(
    thread_id: str, login: str, body: ThreadMessageBody
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login)
    owner, name, _ = _metadata_repo(metadata)
    if not owner or not name:
        raise HTTPException(400, "thread is missing repository metadata")

    prompt = body.content.strip()
    now_ms = _now_ms()
    chosen_model, chosen_effort = _normalize_model_choice(body.model_id, body.effort)
    metadata_update: dict[str, Any] = {"updated_at_ms": now_ms}
    if chosen_model and chosen_effort:
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort
    await client.threads.update(thread_id=thread_id, metadata=metadata_update)

    if await is_thread_active(thread_id):
        queued = await queue_message_for_thread(thread_id, prompt)
        if not queued:
            raise HTTPException(502, "failed to queue follow-up message")
        thread = await client.threads.get(thread_id)
        return _thread_summary(
            thread if isinstance(thread, dict) else {"thread_id": thread_id, "metadata": metadata}
        )

    await _persist_dashboard_github_token(thread_id, login)
    profile = await get_profile(login) or {}
    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": _DASHBOARD_SOURCE,
        "github_login": login,
        "repo": {"owner": owner, "name": name},
        "user_email": profile.get("email"),
    }
    if chosen_model and chosen_effort:
        configurable["agent_model_id"] = chosen_model
        configurable["agent_effort"] = chosen_effort
    run = await client.runs.create(
        thread_id,
        _ASSISTANT_ID,
        input={"messages": [{"role": "user", "content": prompt}]},
        config={"configurable": configurable, "metadata": _agent_version_metadata()},
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


async def cancel_dashboard_thread(thread_id: str, login: str) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login)

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


async def delete_dashboard_thread(thread_id: str, login: str) -> None:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login)

    run_id = metadata.get("latest_run_id")
    if isinstance(run_id, str) and run_id:
        try:
            await client.runs.cancel(thread_id, run_id, wait=False)
        except Exception:
            logger.debug("Could not cancel run %s for thread %s", run_id, thread_id, exc_info=True)

    await client.threads.delete(thread_id)


async def stream_dashboard_thread(thread_id: str, login: str) -> AsyncIterator[str]:
    try:
        thread = await langgraph_client().threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
    _assert_thread_owner(metadata, login)

    stream = await langgraph_client().threads.join_stream(thread_id)
    async for part in stream:
        payload = part if isinstance(part, dict) else {"event": str(part)}
        yield f"data: {json.dumps(payload, default=str)}\n\n"
