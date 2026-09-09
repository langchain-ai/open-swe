"""Dashboard thread detail, messaging and lifecycle endpoints backed by LangGraph."""

import logging
import posixpath
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from agent.dashboard.options import normalize_model_choice
from agent.dashboard.threads.access import (
    _authorized_thread,
    _github_token_for_login,
    _readable_thread_metadata,
)
from agent.dashboard.threads.runs import (
    ThreadMessageBody,
    _build_dashboard_configurable,
    _notify_slack_web_handoff,
    _user_message_content,
)
from agent.dashboard.threads.summary import (
    _DASHBOARD_SOURCE,
    _SANDBOX_CREATING_SENTINEL,
    _assert_thread_postable,
    _assert_thread_readable,
    _is_thread_resolved,
    _metadata_model_id,
    _now_ms,
    _refresh_latest_run_metadata,
    _run_status_to_agent_status,
    _thread_is_busy,
    _thread_run_id,
    _thread_summary,
)
from agent.dispatch import dispatch_agent_run
from agent.github.pull_request_checks import PullRequestState, get_pull_request_check_states
from agent.github.pull_request_context import get_pull_request_context
from agent.github.pull_request_status import get_pull_request_statuses
from agent.slack.client import parse_github_pr_url
from agent.utils.json_types import as_json_object, as_thread_dict, thread_metadata
from agent.utils.thread_ops import (
    get_thread_active_status,
    langgraph_client,
    queue_message_for_thread,
)
from agent.utils.thread_participants import (
    PARTICIPANT_EMAILS_KEY,
    PARTICIPANT_LOGINS_KEY,
    merge_participants,
)
from agent.utils.thread_pr_state import agent_thread_pr_state_lock
from agent.utils.timing import phase

logger = logging.getLogger(__name__)


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


async def get_dashboard_terminal_sandbox(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[str, str | None]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    _assert_thread_readable(metadata)
    sandbox_id = metadata.get("sandbox_id")
    if (
        not isinstance(sandbox_id, str)
        or not sandbox_id
        or sandbox_id == _SANDBOX_CREATING_SENTINEL
    ):
        raise HTTPException(404, "thread sandbox is not ready")
    repo_name = metadata.get("repo_name")
    if not isinstance(repo_name, str) or posixpath.basename(repo_name) != repo_name:
        repo_name = None
    return sandbox_id, repo_name


async def _queued_dashboard_messages(client: Any, thread_id: str) -> list[dict[str, Any]]:
    try:
        item = await client.store.get_item(("queue", thread_id), "pending_messages")
    except Exception:  # noqa: BLE001
        logger.debug(
            "Could not fetch queued messages",
            extra={"thread_id": thread_id},
            exc_info=True,
        )
        return []
    value = item.get("value") if isinstance(item, Mapping) else None
    messages = value.get("messages") if isinstance(value, Mapping) else None
    if not isinstance(messages, list):
        return []

    queued: list[dict[str, Any]] = []
    for entry in messages:
        content = entry.get("content") if isinstance(entry, Mapping) else None
        if not isinstance(content, Mapping) or content.get("source") != _DASHBOARD_SOURCE:
            continue
        queued_id = content.get("queue_id")
        text = content.get("text")
        created_at = content.get("created_at_ms")
        if (
            not isinstance(queued_id, str)
            or not queued_id
            or not isinstance(text, str)
            or not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
        ):
            continue
        images = []
        raw_images = content.get("images")
        if isinstance(raw_images, list):
            for image in raw_images:
                if not isinstance(image, Mapping):
                    continue
                base64_data = image.get("base64")
                mime_type = image.get("mime_type")
                if not isinstance(base64_data, str) or not isinstance(mime_type, str):
                    continue
                mapped_image = {
                    "kind": "image",
                    "base64": base64_data,
                    "mimeType": mime_type,
                }
                file_name = image.get("file_name")
                if isinstance(file_name, str) and file_name:
                    mapped_image["fileName"] = file_name
                images.append(mapped_image)
        queued.append(
            {
                "id": queued_id,
                "content": text,
                "images": images,
                "createdAt": int(created_at),
            }
        )
    return queued


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
    if mark_viewed and status != "running":
        metadata = await _mark_thread_viewed(
            client,
            thread_id,
            metadata,
            latest_run_id=latest_run_id,
        )
        thread = {**as_thread_dict(thread), "metadata": metadata}

    summary = await _thread_summary(
        thread,
        latest_run_status=latest_run_status,
        latest_run_id=latest_run_id,
    )
    if status == "running":
        summary["queuedMessages"] = await _queued_dashboard_messages(client, thread_id)
    return summary


async def send_dashboard_message(
    thread_id: str, login: str, body: ThreadMessageBody, *, email: str | None = None
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    _assert_thread_postable(metadata, login, email)

    prompt = body.content.strip()
    now_ms = _now_ms()
    chosen_model, chosen_effort = normalize_model_choice(body.model_id, body.effort)
    handoff_metadata = dict(metadata)
    metadata_update: dict[str, Any] = {
        "source": _DASHBOARD_SOURCE,
        "updated_at_ms": now_ms,
        "plan_mode": body.plan_mode,
        PARTICIPANT_LOGINS_KEY: merge_participants(metadata.get(PARTICIPANT_LOGINS_KEY), login),
        PARTICIPANT_EMAILS_KEY: merge_participants(metadata.get(PARTICIPANT_EMAILS_KEY), email),
    }
    if chosen_model and chosen_effort:
        metadata_update["model"] = chosen_model
        metadata_update["effort"] = chosen_effort
    pr_linked = any(metadata.get(key) for key in ("pr_url", "pr_urls", "pull_requests"))

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
    if pr_linked or metadata.get("auto_resolved_by_prs") is True:
        async with agent_thread_pr_state_lock(client, thread_id):
            current = await client.threads.get(thread_id)
            metadata = thread_metadata(current)
            if _is_thread_resolved(metadata):
                metadata_update["resolved"] = False
                metadata_update["resolved_at_ms"] = None
            if metadata.get("auto_resolved_by_prs") is True:
                metadata_update["auto_resolved_by_prs"] = False
            if metadata.get("attention_reason"):
                metadata_update["attention_reason"] = None
            await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    else:
        if _is_thread_resolved(metadata):
            metadata_update["resolved"] = False
            metadata_update["resolved_at_ms"] = None
        if metadata.get("attention_reason"):
            metadata_update["attention_reason"] = None
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    queue_payload: dict[str, Any] = {
        "text": prompt,
        "source": _DASHBOARD_SOURCE,
        "surface": "web",
        "queue_id": (
            str(body.client_message_id) if body.client_message_id else f"queued-{uuid.uuid4()}"
        ),
        "created_at_ms": now_ms,
        "sender": {
            "id": f"github:{login}",
            "platform": "github",
            "github_login": login,
            **({"email": email} if email else {}),
        },
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
    return await _thread_summary(thread)


async def _cancel_active_thread_runs(client: Any, thread_id: str) -> None:
    run_ids: set[str] = set()
    for status in ("pending", "running"):
        offset = 0
        while True:
            runs = await client.runs.list(thread_id, status=status, limit=100, offset=offset)
            run_ids.update(
                run_id for run in runs if isinstance((run_id := run.get("run_id")), str) and run_id
            )
            if len(runs) < 100:
                break
            offset += len(runs)
    if run_ids:
        await client.runs.cancel_many(
            thread_id=thread_id,
            run_ids=sorted(run_ids),
            action="interrupt",
        )


async def cancel_dashboard_thread(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Interrupt every live run on a thread on behalf of its owner.

    Cancels by thread rather than by ``latest_run_id`` so the stop button works
    for runs this browser never started (Slack/Linear/GitHub triggers, CI
    auto-fix): the client-side ``stream.stop()`` can only cancel a run it
    dispatched itself, and cached ``latest_run_id`` metadata can lag the run the
    platform is actually executing.
    """
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    _assert_thread_postable(metadata, login, email)

    try:
        await _cancel_active_thread_runs(client, thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to cancel active runs for thread %s", thread_id)
        raise HTTPException(502, "failed to request thread cancellation") from exc

    metadata_update: dict[str, Any] = {
        "latest_run_status": "interrupted",
        "updated_at_ms": _now_ms(),
    }
    await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    queued = await client.store.get_item(("queue", thread_id), "pending_messages")
    queued_messages = queued.get("value", {}).get("messages", []) if queued else []
    if queued_messages:
        try:
            configurable = await _build_dashboard_configurable(thread_id, login, metadata)
            run = await dispatch_agent_run(
                thread_id,
                None,
                configurable,
                source=_DASHBOARD_SOURCE,
                input={"messages": []},
                client=client,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to submit queued follow-up for thread %s", thread_id)
            raise HTTPException(502, "stopped run but failed to submit queued follow-up") from exc
        run_id = run.get("run_id") if isinstance(run, dict) else None
        metadata_update.update(latest_run_status="pending", latest_run_id=run_id)

    if queued_messages:
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    thread = await client.threads.get(thread_id)
    return await _thread_summary(thread)


async def admin_cancel_dashboard_thread(thread_id: str) -> dict[str, Any]:
    client = langgraph_client()
    try:
        await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    try:
        await _cancel_active_thread_runs(client, thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to cancel active runs for thread %s", thread_id)
        raise HTTPException(502, "failed to request thread cancellation") from exc

    await client.threads.update(
        thread_id=thread_id,
        metadata={"latest_run_status": "interrupted", "updated_at_ms": _now_ms()},
    )
    updated_thread = await client.threads.get(thread_id)
    return await _thread_summary(updated_thread)


async def delete_dashboard_thread(thread_id: str, login: str, *, email: str | None = None) -> None:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    _assert_thread_postable(metadata, login, email)

    run_id = metadata.get("latest_run_id")
    if isinstance(run_id, str) and run_id:
        try:
            await client.runs.cancel(thread_id, run_id, wait=False)
        except Exception:
            logger.debug("Could not cancel run %s for thread %s", run_id, thread_id, exc_info=True)

    await client.threads.delete(thread_id)


async def rename_dashboard_thread(
    thread_id: str, login: str, *, title: str, email: str | None = None
) -> dict[str, Any]:
    client = langgraph_client()
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata_update = {"title": title, "title_seed": None}
    try:
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not rename thread", extra={"thread_id": thread_id}, exc_info=True)
        raise HTTPException(502, "failed to update thread") from exc
    thread = {
        **as_thread_dict(thread),
        "metadata": {**thread_metadata(thread), **metadata_update},
    }
    return await _thread_summary(thread)


async def resolve_dashboard_thread(
    thread_id: str, login: str, *, resolved: bool, email: str | None = None
) -> dict[str, Any]:
    """Mark a thread resolved/unresolved via thread metadata."""
    client = langgraph_client()
    await _authorized_thread(thread_id, login, email=email)
    try:
        async with agent_thread_pr_state_lock(client, thread_id):
            thread = await _authorized_thread(thread_id, login, email=email)
            metadata = thread_metadata(thread)
            metadata_update: dict[str, Any] = {
                "resolved": resolved,
                "resolved_at_ms": _now_ms() if resolved else None,
                "auto_resolved_by_prs": False,
                "attention_reason": None,
            }
            await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not update resolved state for thread %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to update thread") from exc
    thread = {**as_thread_dict(thread), "metadata": {**metadata, **metadata_update}}
    return await _thread_summary(thread)


def _tracked_pull_requests(metadata: Mapping[str, Any]) -> list[object]:
    records = metadata.get("pull_requests")
    tracked = list(records) if isinstance(records, list) else []
    if tracked:
        return tracked
    pr_url = metadata.get("pr_url")
    pr_ref = parse_github_pr_url(pr_url) if isinstance(pr_url, str) else None
    if not pr_ref:
        return []
    return [
        {
            "repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
            "number": pr_ref.number,
        }
    ]


async def get_dashboard_thread_pull_request_status(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Return live GitHub health for every pull request tracked by the thread."""
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    tracked = _tracked_pull_requests(metadata)
    if not tracked:
        return {"pullRequests": []}
    token = await _github_token_for_login(login)
    return {"pullRequests": await get_pull_request_statuses(tracked, token)}


async def get_dashboard_pull_request_checks(
    records: Sequence[object], login: str
) -> dict[str, PullRequestState]:
    """Return batched live state for the pull requests the sidebar is showing."""
    if not records:
        return {}
    token = await _github_token_for_login(login)
    return dict(await get_pull_request_check_states(records, login, token))


async def get_dashboard_thread_pull_request_context(
    thread_id: str,
    login: str,
    *,
    repo_full_name: str,
    number: int,
    email: str | None = None,
) -> dict[str, Any]:
    """Return fresh model context for one PR already tracked by the thread."""
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    record = next(
        (
            candidate
            for candidate in _tracked_pull_requests(metadata)
            if isinstance(candidate, Mapping)
            and candidate.get("repo_full_name") == repo_full_name
            and candidate.get("number") == number
        ),
        None,
    )
    if record is None:
        raise HTTPException(404, "pull request is not tracked by this thread")
    token = await _github_token_for_login(login)
    result = await get_pull_request_context(record, token)
    if result is None:
        raise HTTPException(502, "could not scan pull request")
    return result


async def get_dashboard_thread_state(
    thread_id: str,
    login: str,
    *,
    email: str | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    record = timings if timings is not None else {}
    client = langgraph_client()
    with phase(record, "thread_get"):
        try:
            thread = await client.threads.get(thread_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    _assert_thread_readable(metadata)
    thread, latest_run_status, _ = await _refresh_latest_run_metadata(
        client, thread, timings=record
    )
    metadata = thread_metadata(thread)
    with phase(record, "get_state"):
        state = await client.threads.get_state(thread_id)
    result = as_json_object(state)
    # The SDK's `useStream` opens its live event subscription only when the
    # hydrated `getState()` looks active (`next` non-empty / absent). When a
    # run was just started out-of-band (our REST run-create), the latest
    # checkpoint can still be the previous finished one with `next == []`,
    # which the SDK reads as idle and never opens the stream. Drop `next`
    # while a run is pending/running so the SDK treats the thread as active.
    metadata_run_status = metadata.get("latest_run_status")
    if (
        _thread_is_busy(thread)
        or latest_run_status in {"pending", "running"}
        or metadata_run_status in {"pending", "running"}
    ):
        result.pop("next", None)
    return result
