"""Backend for the review page's "chat with this PR" feature.

A dedicated, sandbox-less ``chat`` graph (``agent/chat.py``) answers questions
about one PR. This module mints a per-user chat thread, seeds the PR diff,
review findings, and an overview as virtual files on the first run, and proxies
the LangGraph stream/commands/state/history protocol the frontend SDK speaks —
the chat counterpart of ``thread_api``'s agent proxy, pinned to assistant
``chat`` and scoped to the review's PR.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from deepagents.backends.utils import create_file_data
from fastapi import HTTPException

from ..reviewer_diff import fetch_pr_diff
from ..reviewer_findings import REVIEWER_THREAD_KIND
from ..utils.github_app import get_github_app_installation_token
from ..utils.thread_ops import langgraph_client, langgraph_url
from .options import SUPPORTED_MODEL_IDS, model_supports_effort
from .review_api import classify_finding, get_pr_head_sha, get_review, reviewer_thread_id
from .thread_api import (
    _DASHBOARD_STREAM_MODES,
    _langgraph_proxy_headers,
    _require_json_content_type,
    _stream_thread_events,
)

logger = logging.getLogger(__name__)

_CHAT_ASSISTANT_ID = "chat"
_CHAT_SOURCE = "review_chat"
# Sentinel: caller did not pre-fetch the thread metadata, so fetch it here.
_UNFETCHED = object()
_PROXY_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_MAX_DIFF_CHARS = 400_000


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


_TITLE_MAX_CHARS = 60


async def _reviewer_thread_exists(owner: str, repo: str, pr_number: int) -> bool:
    try:
        thread = await langgraph_client().threads.get(reviewer_thread_id(owner, repo, pr_number))
    except Exception:  # noqa: BLE001
        return False
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    return isinstance(metadata, dict) and metadata.get("kind") == REVIEWER_THREAD_KIND


async def get_review_chat(owner: str, repo: str, pr_number: int, login: str) -> dict[str, Any]:
    """Chat availability for this PR. Threads are minted client-side per chat."""
    return {
        "available": await _reviewer_thread_exists(owner, repo, pr_number),
        "assistant_id": _CHAT_ASSISTANT_ID,
    }


def _chat_thread_search_metadata(
    owner: str, repo: str, pr_number: int, login: str
) -> dict[str, Any]:
    return {
        "kind": _CHAT_SOURCE,
        "github_login": login,
        "repo_owner": owner,
        "repo_name": repo,
        "pr_number": pr_number,
    }


async def list_review_chat_threads(
    owner: str, repo: str, pr_number: int, login: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """This user's chat conversations for the PR, newest first."""
    client = langgraph_client()
    try:
        threads = await client.threads.search(
            metadata=_chat_thread_search_metadata(owner, repo, pr_number, login),
            limit=limit,
            sort_by="updated_at",
            sort_order="desc",
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "chat thread search failed for %s/%s#%s", owner, repo, pr_number, exc_info=True
        )
        return []
    out: list[dict[str, Any]] = []
    for thread in threads or []:
        if not isinstance(thread, dict):
            continue
        metadata = thread.get("metadata") if isinstance(thread.get("metadata"), dict) else {}
        thread_id = thread.get("thread_id") or thread.get("id")
        if not isinstance(thread_id, str):
            continue
        out.append(
            {
                "thread_id": thread_id,
                "title": metadata.get("title") or "New chat",
                "updated_at": thread.get("updated_at")
                if isinstance(thread.get("updated_at"), str)
                else None,
            }
        )
    return out


async def delete_review_chat_thread(
    owner: str, repo: str, pr_number: int, login: str, thread_id: str
) -> None:
    """Delete one of the user's chat threads (scoped + ownership-checked)."""
    metadata = await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    if metadata is None:
        return  # already gone; treat as success
    await langgraph_client().threads.delete(thread_id)


def _first_user_text(params: dict[str, Any]) -> str:
    run_input = params.get("input")
    messages = run_input.get("messages") if isinstance(run_input, dict) else None
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "human":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
    return ""


def _derive_title(params: dict[str, Any]) -> str:
    text = _first_user_text(params)
    if not text:
        return "New chat"
    flattened = " ".join(text.split())
    return flattened[:_TITLE_MAX_CHARS] if flattened else "New chat"


def _render_overview(review: dict[str, Any]) -> str:
    pr = review.get("pr") if isinstance(review.get("pr"), dict) else {}
    lines = [
        f"# {review.get('title') or 'Pull request'} (#{review.get('number')})",
        "",
        f"- Repository: {review.get('full_name', '')}",
        f"- Author: {review.get('author', '')}",
        f"- Head: {review.get('head_ref', '')} @ {review.get('head_sha', '')[:12]}",
        f"- Base: {review.get('base_ref', '')}",
        f"- State: {pr.get('state', '')}",
        f"- Changes: +{pr.get('additions', 0)} -{pr.get('deletions', 0)} "
        f"across {pr.get('changed_files', 0)} file(s), {pr.get('commits', 0)} commit(s)",
        "",
        "## Description",
        "",
        str(pr.get("body") or "_No description provided._"),
    ]
    return "\n".join(lines)


def _render_findings(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "# Review findings\n\n_No findings were published for this PR._"
    out = ["# Review findings", ""]
    for finding in findings:
        group = classify_finding(finding)
        location = finding.get("file") or ""
        start = finding.get("start_line")
        if location and start:
            location = f"{location}:{start}"
        out.append(
            f"## [{group}] {finding.get('title') or 'Untitled'} "
            f"({finding.get('severity', 'low')}/{finding.get('confidence', 'medium')}, "
            f"{finding.get('status', 'open')})"
        )
        if location:
            out.append(f"`{location}`")
        out.append("")
        out.append(str(finding.get("description") or ""))
        suggestion = finding.get("suggestion")
        if suggestion:
            out.append("")
            out.append(f"Suggested change:\n```\n{suggestion}\n```")
        note = finding.get("resolution_note")
        if note:
            out.append("")
            out.append(f"Resolution: {note}")
        out.append("")
    return "\n".join(out)


def _review_head_sha(review: dict[str, Any]) -> str:
    pr = review.get("pr") if isinstance(review.get("pr"), dict) else {}
    return str(pr.get("head_sha") or review.get("head_sha") or "")


async def _build_pr_context(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    *,
    review: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Fetch diff + findings + overview as seedable files; return ``(files, head_sha)``.

    Accepts an already-fetched ``review`` to avoid re-fetching it when the caller
    has just read it to decide whether a reseed is needed.
    """
    if review is None:
        review = await get_review(owner, repo, pr_number)
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    head_sha = _review_head_sha(review)
    diff = await fetch_pr_diff(owner=owner, repo=repo, pr_number=pr_number, token=token) or ""
    if len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n\n[diff truncated]\n"
    files = {
        "/pr/overview.md": create_file_data(_render_overview(review)),
        "/pr/diff.patch": create_file_data(diff or "[diff unavailable]"),
        "/pr/findings.md": create_file_data(_render_findings(findings)),
    }
    return files, head_sha


async def _get_chat_thread_metadata(thread_id: str) -> dict[str, Any] | None:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        return None
    return thread.get("metadata") if isinstance(thread, dict) else None


async def assert_chat_thread_access(
    thread_id: str, owner: str, repo: str, pr_number: int, login: str
) -> dict[str, Any] | None:
    """Authorize a client-supplied chat thread id before proxying to LangGraph.

    Chat threads are private per viewer and their ids come from the client, so
    every proxy route must confirm the caller owns the thread it names. Returns
    the thread metadata when it exists and belongs to ``login`` for this PR, or
    ``None`` when the thread doesn't exist yet (it's created lazily on the first
    run, so there is nothing to leak). Raises 404 when a thread exists but is
    owned by someone else or scoped to a different repo/PR — this also rejects
    reviewer (or any non-chat) threads, whose ``kind`` is not ``_CHAT_SOURCE``.
    """
    metadata = await _get_chat_thread_metadata(thread_id)
    if metadata is None:
        return None
    owns = (
        metadata.get("kind") == _CHAT_SOURCE
        and metadata.get("github_login") == login
        and metadata.get("repo_owner") == owner
        and metadata.get("repo_name") == repo
        and metadata.get("pr_number") == pr_number
    )
    if not owns:
        raise HTTPException(404, "chat not found")
    return metadata


async def _create_chat_thread(
    thread_id: str, owner: str, repo: str, pr_number: int, login: str, *, title: str
) -> None:
    now_ms = _now_ms()
    metadata = {
        "kind": _CHAT_SOURCE,
        "source": _CHAT_SOURCE,
        "github_login": login,
        "repo_owner": owner,
        "repo_name": repo,
        "pr_number": pr_number,
        "title": title,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
    }
    await langgraph_client().threads.create(
        thread_id=thread_id, metadata=metadata, if_exists="do_nothing"
    )


def _normalize_chat_model(configurable: dict[str, Any]) -> tuple[str | None, str | None]:
    model_id = configurable.get("chat_model_id")
    effort = configurable.get("chat_effort")
    if (
        isinstance(model_id, str)
        and model_id in SUPPORTED_MODEL_IDS
        and isinstance(effort, str)
        and model_supports_effort(model_id, effort)
    ):
        return model_id, effort
    return None, None


async def _enrich_chat_command(
    command: dict[str, Any],
    *,
    owner: str,
    repo: str,
    pr_number: int,
    login: str,
    thread_id: str,
    thread_metadata: Any = _UNFETCHED,
) -> dict[str, Any]:
    if command.get("method") != "run.start":
        return command

    params = command.get("params")
    if not isinstance(params, dict):
        params = {}
        command["params"] = params

    # Reuse the metadata the access check already fetched to avoid a duplicate
    # thread read on the hot path; fall back to fetching it when not supplied.
    if thread_metadata is _UNFETCHED:
        thread_metadata = await _get_chat_thread_metadata(thread_id)
    metadata = thread_metadata
    created = metadata is None
    if created:
        await _create_chat_thread(
            thread_id, owner, repo, pr_number, login, title=_derive_title(params)
        )
        metadata = {}

    client_config = params.get("config")
    if not isinstance(client_config, dict):
        client_config = {}
    client_configurable = client_config.get("configurable")
    if not isinstance(client_configurable, dict):
        client_configurable = {}

    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "source": _CHAT_SOURCE,
        "github_login": login,
        "chat_repo_owner": owner,
        "chat_repo_name": repo,
        "chat_pr_number": pr_number,
        "reviewer_thread_id": reviewer_thread_id(owner, repo, pr_number),
    }
    model_id, effort = _normalize_chat_model(client_configurable)
    if model_id and effort:
        configurable["chat_model_id"] = model_id
        configurable["chat_effort"] = effort

    # Seed PR context on the thread's first run, and reseed whenever the PR head
    # has moved since the last seed — otherwise the chat keeps answering from a
    # stale diff/findings while the review page already shows the current head.
    stored_head = metadata.get("chat_head_sha") if isinstance(metadata, dict) else None
    stored_head = stored_head if isinstance(stored_head, str) else ""

    # Detect head movement with a single lightweight PR lookup instead of a full
    # ``get_review`` (which also fetches check runs + the reviewer thread). The
    # full review is only fetched below, by ``_build_pr_context``, when we
    # actually need to reseed. On lookup failure, keep the existing context.
    review: dict[str, Any] | None = None
    needs_seed = created
    if not created:
        current_head = await get_pr_head_sha(owner, repo, pr_number)
        needs_seed = bool(current_head) and current_head != stored_head

    if needs_seed:
        token = await get_github_app_installation_token(repositories=[repo])
        if not token:
            raise HTTPException(503, "GitHub App token unavailable")
        try:
            pr_files, head_sha = await _build_pr_context(
                owner, repo, pr_number, token, review=review
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to seed PR chat context for %s/%s#%s", owner, repo, pr_number)
            raise HTTPException(502, "could not load PR context") from exc
        if head_sha:
            configurable["chat_head_sha"] = head_sha
            await langgraph_client().threads.update(
                thread_id=thread_id, metadata={"chat_head_sha": head_sha}
            )
        elif stored_head:
            configurable["chat_head_sha"] = stored_head
        run_input = params.get("input")
        if not isinstance(run_input, dict):
            run_input = {}
        existing_files = run_input.get("files")
        run_input["files"] = {
            **(existing_files if isinstance(existing_files, dict) else {}),
            **pr_files,
        }
        params["input"] = run_input
    elif stored_head:
        configurable["chat_head_sha"] = stored_head

    params["assistant_id"] = _CHAT_ASSISTANT_ID
    params.setdefault("stream_mode", list(_DASHBOARD_STREAM_MODES))
    params.setdefault("stream_resumable", True)
    params["config"] = {**client_config, "configurable": configurable}
    command["params"] = params
    return command


async def proxy_review_chat_commands(
    owner: str,
    repo: str,
    pr_number: int,
    login: str,
    thread_id: str,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    # Reject threads the caller doesn't own; a missing thread is created lazily
    # below on the first `run.start` (with the caller as owner). Reuse the
    # fetched metadata to avoid a second thread read during enrichment.
    metadata = await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    _require_json_content_type(content_type)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "command body must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(400, "command body must be a JSON object")

    enriched = await _enrich_chat_command(
        parsed,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        login=login,
        thread_id=thread_id,
        thread_metadata=metadata,
    )
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/commands"
    headers = _langgraph_proxy_headers(content_type=content_type)
    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        response = await client.post(url, content=json.dumps(enriched).encode(), headers=headers)
    return response.status_code, response.content, response.headers.get("content-type")


async def proxy_review_chat_stream_events(
    owner: str,
    repo: str,
    pr_number: int,
    login: str,
    thread_id: str,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> AsyncIterator[bytes]:
    await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    _require_json_content_type(content_type)
    return _stream_thread_events(thread_id, body, content_type)


async def _proxy_passthrough(
    method: str, thread_id: str, suffix: str, body: bytes | None, content_type: str
) -> tuple[int, bytes, str | None]:
    url = f"{langgraph_url().rstrip('/')}/threads/{thread_id}/{suffix}"
    headers = _langgraph_proxy_headers(content_type=content_type)
    async with httpx.AsyncClient(timeout=_PROXY_REQUEST_TIMEOUT) as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        else:
            response = await client.post(url, content=body or b"{}", headers=headers)
    return response.status_code, response.content, response.headers.get("content-type")


async def proxy_review_chat_state(
    owner: str, repo: str, pr_number: int, login: str, thread_id: str
) -> tuple[int, bytes, str | None]:
    await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    status_code, content, media_type = await _proxy_passthrough(
        "GET", thread_id, "state", None, "application/json"
    )
    # The chat thread is created lazily on the first run, so an initial getState
    # hits a missing thread. Return an empty idle state so the SDK hydrates a
    # fresh thread instead of surfacing the 404 as a hard error.
    if status_code == 404:
        empty = json.dumps({"values": {}, "next": []}).encode()
        return 200, empty, "application/json"
    return status_code, content, media_type


async def proxy_review_chat_history(
    owner: str,
    repo: str,
    pr_number: int,
    login: str,
    thread_id: str,
    body: bytes,
    *,
    content_type: str = "application/json",
) -> tuple[int, bytes, str | None]:
    await assert_chat_thread_access(thread_id, owner, repo, pr_number, login)
    _require_json_content_type(content_type)
    status_code, content, media_type = await _proxy_passthrough(
        "POST", thread_id, "history", body, content_type
    )
    # The thread is created lazily on the first run; before then, hydration
    # history reads hit a missing thread. Return an empty list so the SDK
    # treats it as a fresh thread instead of erroring.
    if status_code == 404:
        return 200, b"[]", "application/json"
    return status_code, content, media_type
