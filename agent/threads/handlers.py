"""Dashboard thread detail, messaging and lifecycle endpoints backed by LangGraph."""

import asyncio
import logging
import posixpath
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException
from langgraph_sdk.errors import NotFoundError

from agent.dashboard.options import normalize_model_choice
from agent.github.pull_request_checks import PullRequestState, get_pull_request_check_states
from agent.github.pull_request_context import get_pull_request_context
from agent.github.pull_request_status import get_pull_request_statuses
from agent.review.session import ReviewSession, ReviewSessionMetadata
from agent.slack.client import parse_github_pr_url
from agent.threads.access import (
    _authorized_thread,
    _github_token_for_login,
    _readable_thread_metadata,
)
from agent.threads.listing import list_unresolved_dashboard_threads, settle_review_walkthrough
from agent.threads.runs import (
    _ASSISTANT_ID,
    QUEUED_BY_KEY,
    ThreadMessageBody,
    _notify_slack_web_handoff,
    _user_message_content,
    dispatch_pending_follow_ups,
)
from agent.threads.summary import (
    _SANDBOX_CREATING_SENTINEL,
    DASHBOARD_SOURCE,
    _assert_thread_postable,
    _assert_thread_promptable,
    _is_thread_resolved,
    _metadata_model_id,
    _now_ms,
    _refresh_latest_run_metadata,
    _thread_is_busy,
    _thread_run_id,
    _thread_summary,
    assert_thread_readable,
    run_status_to_agent_status,
    thread_source,
)
from agent.transcript.engine import delete_transcript
from agent.transcript.mirror import mirror_thread_metadata
from agent.transcript.turns import settle_run_turn
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
from agent.utils.thread_settings import THREAD_SETTINGS_KEY
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


async def mark_review_session_viewed(review: ReviewSession) -> None:
    """Clear the review's unread dot; a no-op when the user has not listed this review."""
    client = langgraph_client()
    try:
        thread = await client.threads.get(review.thread_id)
    except NotFoundError:
        return
    metadata = thread_metadata(thread)
    session = ReviewSessionMetadata.parse(metadata)
    if session is None or not session.owned_by(review.login):
        return
    # Settled first so a walkthrough the user is looking at is not recorded as newer than the view.
    thread = await settle_review_walkthrough(client, thread)
    thread, _, latest_run_id = await _refresh_latest_run_metadata(client, thread)
    await _mark_thread_viewed(
        client, review.thread_id, thread_metadata(thread), latest_run_id=latest_run_id
    )


async def get_dashboard_terminal_sandbox(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[str, str | None]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc
    metadata = thread_metadata(thread)
    # A shell is a live capability into the owner's sandbox, so admins who may
    # view a private thread still cannot open one.
    _assert_thread_promptable(metadata, login)
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


async def get_dashboard_thread(
    thread_id: str,
    login: str,
    *,
    email: str | None = None,
    mark_viewed: bool = True,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    record = timings if timings is not None else {}
    client = langgraph_client()
    with phase(record, "thread_get"):
        try:
            thread = await client.threads.get(thread_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Thread lookup failed for %s", thread_id, exc_info=True)
            raise HTTPException(404, "thread not found") from exc

    metadata = thread_metadata(thread)
    assert_thread_readable(metadata, login, email)

    # The transcript is hydrated client-side by the SDK (`StreamProvider` reads
    # `GET …/state` → `stream.messages`), so the detail endpoint returns
    # metadata only — no server-side message conversion.
    thread, latest_run_status, latest_run_id = await _refresh_latest_run_metadata(
        client, thread, timings=record
    )
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
    if mark_viewed and status != "running":
        with phase(record, "mark_viewed"):
            metadata = await _mark_thread_viewed(
                client,
                thread_id,
                metadata,
                latest_run_id=latest_run_id,
            )
        thread = {**as_thread_dict(thread), "metadata": metadata}

    with phase(record, "summary"):
        summary = await _thread_summary(
            thread,
            latest_run_status=latest_run_status,
            latest_run_id=latest_run_id,
        )
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
        "source": DASHBOARD_SOURCE,
        # Continuing on the web promotes a `/oswe` question thread for good.
        "unlisted": False,
        "updated_at_ms": now_ms,
        "feedback_last_activity_at_ms": now_ms,
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
    await mirror_thread_metadata(thread_id, metadata_update)
    queue_payload: dict[str, Any] = {
        "text": prompt,
        "source": DASHBOARD_SOURCE,
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


async def _cancel_active_thread_runs(
    client: Any, thread_id: str, *, stopped_by: str | None = None
) -> tuple[list[str], bool]:
    """Interrupt every live run on the thread, and report which ones those were.

    With ``stopped_by``, a follow-up someone else queued is left to run: their
    message would otherwise vanish, since only the stopper gets theirs back.
    Also reports whether any such run was kept.
    """
    run_ids: set[str] = set()
    kept = False
    for status in ("pending", "running"):
        offset = 0
        while True:
            runs = await client.runs.list(thread_id, status=status, limit=100, offset=offset)
            for run in runs:
                run_id = run.get("run_id")
                if not isinstance(run_id, str) or not run_id:
                    continue
                queued_by = (run.get("metadata") or {}).get(QUEUED_BY_KEY)
                if stopped_by is not None and queued_by not in {None, stopped_by}:
                    kept = True
                    continue
                run_ids.add(run_id)
            if len(runs) < 100:
                break
            offset += len(runs)
    cancelled = sorted(run_ids)
    if cancelled:
        await client.runs.cancel_many(
            thread_id=thread_id,
            run_ids=cancelled,
            action="interrupt",
        )
    return cancelled, kept


async def interrupt_transcript_turns(thread_id: str, run_ids: Sequence[str]) -> None:
    """Close the transcript turn of each cancelled run.

    Scoped to the runs that were actually cancelled: a queued follow-up
    dispatched right after this must not have its own freshly opened turn
    settled as interrupted.
    """
    for run_id in run_ids:
        try:
            await settle_run_turn(thread_id, run_id, outcome="interrupted")
        except Exception:  # noqa: BLE001
            # The run is already cancelled; the completion webhook closes the turn.
            logger.warning(
                "Could not record a cancel on the transcript",
                exc_info=True,
                extra={"transcript": {"thread_id": thread_id, "run_id": run_id}},
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
        cancelled_run_ids, kept_queued = await _cancel_active_thread_runs(
            client, thread_id, stopped_by=login
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to cancel active runs for thread %s", thread_id)
        raise HTTPException(502, "failed to request thread cancellation") from exc
    await interrupt_transcript_turns(thread_id, cancelled_run_ids)

    metadata_update: dict[str, Any] = {
        "latest_run_status": "interrupted",
        "updated_at_ms": _now_ms(),
    }
    await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    # A follow-up left queued picks the leftovers up with its first model call.
    try:
        run_id = (
            None
            if kept_queued
            else await dispatch_pending_follow_ups(thread_id, login, metadata, client=client)
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to submit queued follow-up for thread %s", thread_id)
        raise HTTPException(502, "stopped run but failed to submit queued follow-up") from exc
    if run_id is not None:
        metadata_update.update(latest_run_status="pending", latest_run_id=run_id)
        await client.threads.update(thread_id=thread_id, metadata=metadata_update)
    thread = await client.threads.get(thread_id)
    return await _thread_summary(thread)


async def admin_cancel_dashboard_thread(
    thread_id: str, login: str | None = None, *, email: str | None = None
) -> dict[str, Any]:
    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "thread not found") from exc

    if thread_source(thread_metadata(thread)) == "incidents_agent":
        raise HTTPException(404, "thread not found")

    if thread_metadata(thread).get("visibility", "public") != "public":
        assert_thread_readable(thread_metadata(thread), login, email)

    try:
        cancelled_run_ids, _ = await _cancel_active_thread_runs(client, thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to cancel active runs for thread %s", thread_id)
        raise HTTPException(502, "failed to request thread cancellation") from exc
    await interrupt_transcript_turns(thread_id, cancelled_run_ids)

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
    # The mirrored transcript outlives the LangGraph thread otherwise, and the
    # read path authorizes against the mirror rather than against LangGraph.
    try:
        await delete_transcript(thread_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not delete the thread transcript",
            exc_info=True,
            extra={"transcript": {"thread_id": thread_id}},
        )


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
    await mirror_thread_metadata(thread_id, metadata_update)
    thread = {
        **as_thread_dict(thread),
        "metadata": {**thread_metadata(thread), **metadata_update},
    }
    return await _thread_summary(thread)


# Thread settings that carry over into a private continuation. Source linkage
# (Slack, Linear, GitHub, schedules), participants, sandbox, and run state do not.
_CONTINUED_METADATA_KEYS = (
    "title",
    "base_branch",
    "branch_prefix",
    "model",
    "effort",
    "resolved_model",
    "resolved_effort",
    "repo_owner",
    "repo_name",
    "repo_explicitly_none",
    THREAD_SETTINGS_KEY,
)


def _continued_workspace(metadata: Mapping[str, Any]) -> str | None:
    """The workspace to carry into a private continuation; ``environment`` is the pre-workspace key."""
    for key in ("workspace", "environment"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def continue_thread_privately(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Copy a collaborative transcript into a new private thread owned by the caller."""
    client = langgraph_client()
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    if metadata.get("visibility", "public") != "public":
        raise HTTPException(409, "thread is already private")
    state = as_json_object(await client.threads.get_state(thread_id))
    values = state.get("values")
    messages = values.get("messages") if isinstance(values, Mapping) else None
    copied: list[dict[str, Any]] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        extra = message.get("additional_kwargs")
        copied.append(
            {
                **message,
                "additional_kwargs": {
                    **(extra if isinstance(extra, dict) else {}),
                    "collaborative_origin_thread_id": thread_id,
                },
            }
        )

    now_ms = _now_ms()
    new_metadata: dict[str, Any] = {
        key: metadata[key] for key in _CONTINUED_METADATA_KEYS if metadata.get(key) is not None
    }
    workspace = _continued_workspace(metadata)
    if workspace is not None:
        new_metadata["workspace"] = workspace
    new_metadata.update(
        {
            "source": DASHBOARD_SOURCE,
            "origin": DASHBOARD_SOURCE,
            "owner_type": "user",
            "owner_login": login.strip(),
            "visibility": "private",
            "continued_from_thread_id": thread_id,
            "thread_category": "interactive",
            "trigger_kind": "user",
            PARTICIPANT_LOGINS_KEY: merge_participants(None, login),
            PARTICIPANT_EMAILS_KEY: merge_participants(None, email),
            "title": new_metadata.get("title") or "Private continuation",
            "created_at_ms": now_ms,
            "updated_at_ms": now_ms,
            # update_state refuses a thread with no graph, and LangGraph only
            # stamps graph_id once a run has happened.
            "graph_id": metadata.get("graph_id") or _ASSISTANT_ID,
        }
    )
    new_thread_id = str(uuid.uuid4())
    await client.threads.create(thread_id=new_thread_id, metadata=new_metadata, if_exists="raise")
    if copied:
        try:
            await client.threads.update_state(new_thread_id, values={"messages": copied})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not copy transcript into private continuation",
                extra={"source_thread_id": thread_id, "thread_id": new_thread_id},
                exc_info=True,
            )
            try:
                await client.threads.delete(new_thread_id)
            finally:
                raise HTTPException(502, "failed to copy the thread transcript") from exc
    return await _thread_summary(await client.threads.get(new_thread_id))


async def resolve_all_dashboard_threads(login: str, *, email: str | None = None) -> int:
    """Resolve every thread the caller has participated in."""
    client = langgraph_client()
    threads = await list_unresolved_dashboard_threads(login, email=email)
    now_ms = _now_ms()

    async def resolve(thread_id: str) -> None:
        async with agent_thread_pr_state_lock(client, thread_id):
            await client.threads.update(
                thread_id=thread_id,
                metadata={
                    "resolved": True,
                    "resolved_at_ms": now_ms,
                    "auto_resolved_by_prs": False,
                    "attention_reason": None,
                },
            )

    await asyncio.gather(*(resolve(thread["thread_id"]) for thread in threads))
    return len(threads)


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
            if resolved:
                from agent.analytics.emitter import task_accepted, task_marked_complete

                await task_marked_complete(thread_id, source="dashboard")
                await task_accepted(thread_id, source="dashboard", actor_key=login)
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
    assert_thread_readable(metadata, login, email)
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
