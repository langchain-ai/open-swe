"""Run-completion webhook handler — guarantees every run ends with a signal.

The platform POSTs a run-completion payload to ``/webhooks/run-complete`` (wired
as the ``webhook`` on every dispatched run, see ``agent.dispatch``). Successful
Slack runs enqueue deferred session-cost enrichment; failures (``error`` /
``timeout``) post a short reply so a run that died never leaves the user silent.

This decouples "the user gets an answer" from "the agent remembered to reply."
The reply is idempotent per run when the webhook includes a run id. Older or
manual payloads without a run id fall back to legacy thread-level idempotence so
missing ids degrade dedupe instead of silencing failure replies.
"""

import hmac
import logging
import os
from typing import Any

from .dashboard.thread_registry import RunStatus, get_thread_registry
from .dashboard.thread_transcript import messages_to_ui
from .review.findings import REVIEWER_THREAD_KIND
from .review.publish import settle_review_check_run
from .session_cost import schedule_session_cost_refresh
from .utils.dashboard_links import dashboard_thread_url
from .utils.github_app import get_github_app_installation_token
from .utils.github_comments import post_github_comment
from .utils.linear import comment_on_linear_issue
from .utils.slack import post_slack_thread_reply
from .utils.thread_ops import langgraph_client
from .utils.user_messages import warning

logger = logging.getLogger(__name__)

# Run statuses that mean the user will otherwise get nothing back. "interrupted"
# is intentionally excluded: with multitask_strategy="interrupt", a normal
# follow-up halts the prior run (status "interrupted") while its replacement
# carries on — that's healthy, not a failure worth a "couldn't finish" reply.
_TERMINAL_FAILURE_STATUSES = frozenset({"error", "timeout"})
_FAILURE_REPLY_FLAG = "failure_reply_posted"
_FAILURE_REPLY_RUN_ID = "failure_reply_posted_run_id"
_FAILURE_REPLY_RUN_IDS = "failure_reply_posted_run_ids"
_MAX_FAILURE_REPLY_RUN_IDS = 20
_SESSION_COST_REFRESH_RUN_ID = "session_cost_refresh_scheduled_run_id"
_SESSION_COST_REFRESH_RUN_IDS = "session_cost_refresh_scheduled_run_ids"
_MAX_SESSION_COST_REFRESH_RUN_IDS = 20

# Shared-secret bearer token proving a /webhooks/run-complete call came from our
# own dispatch (which appends ?token= when this is set) rather than from an
# attacker hitting the public route. Fail closed when unset: the route rejects
# every call, so completion replies stay off until the secret is configured.
RUN_COMPLETE_WEBHOOK_SECRET = os.environ.get("RUN_COMPLETE_WEBHOOK_SECRET")
if not RUN_COMPLETE_WEBHOOK_SECRET:
    logger.warning(
        "RUN_COMPLETE_WEBHOOK_SECRET is not set; /webhooks/run-complete is fail-closed "
        "(all calls rejected) and run-failure replies are disabled. Set it to enable them."
    )


def verify_run_complete_token(token: str | None) -> bool:
    """Return whether a run-completion webhook token is acceptable.

    Fail closed: with no secret configured, reject every call rather than accept
    unauthenticated requests on a publicly reachable route.
    """
    secret = RUN_COMPLETE_WEBHOOK_SECRET
    if not secret:
        return False
    return token is not None and hmac.compare_digest(token, secret)


def _failure_text(status: str, dashboard_url: str | None = None) -> str:
    if status == "timeout":
        reason = "timed out"
    elif status == "interrupted":
        reason = "was interrupted before it could finish"
    else:
        reason = "hit an unexpected error"
    text = warning(
        f"Open SWE wasn't able to finish that — the run {reason}. "
        "Send another message and it will pick this back up."
    )
    if dashboard_url:
        text += f" You can view the error in <{dashboard_url}|Open SWE Web>."
    return text


async def _settle_failed_reviewer_check(thread_id: str, metadata: dict[str, Any]) -> None:
    """Best-effort cleanup for reviewer checks left open by graph failures."""
    if metadata.get("kind") != REVIEWER_THREAD_KIND:
        return
    if not isinstance(metadata.get("review_check_run_id"), int):
        return
    pr = metadata.get("pr")
    if not isinstance(pr, dict):
        return
    owner = pr.get("owner")
    repo = pr.get("name")
    if not isinstance(owner, str) or not owner or not isinstance(repo, str) or not repo:
        return
    try:
        token = await get_github_app_installation_token()
        if not token:
            logger.warning("run-complete: no GitHub token to settle review check for %s", thread_id)
            return
        pending = metadata.get("review_check_pending_result")
        if isinstance(pending, dict) and pending.get("conclusion") in {
            "success",
            "neutral",
            "failure",
        }:
            conclusion = pending["conclusion"]
            title = str(pending.get("title") or "Review completed")
            summary = str(pending.get("summary") or "")
        else:
            conclusion = "neutral"
            title = "Review did not complete"
            summary = (
                "The Open SWE review run ended without publishing a review. "
                "Re-trigger the review by pushing a commit or re-requesting it."
            )
        await settle_review_check_run(
            thread_id=thread_id,
            owner=owner,
            repo=repo,
            token=token,
            conclusion=conclusion,
            title=title,
            summary=summary,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "run-complete: could not settle review check for %s", thread_id, exc_info=True
        )


async def _post_failure_reply(thread_id: str, metadata: dict[str, Any], status: str) -> bool:
    """Post a failure reply to the run's originating channel. Best-effort."""
    source = metadata.get("source")
    ctx = metadata.get("source_context")
    ctx = ctx if isinstance(ctx, dict) else {}
    text = _failure_text(status)

    slack_thread = ctx.get("slack_thread")
    if source == "slack" or isinstance(slack_thread, dict):
        if isinstance(slack_thread, dict):
            channel_id = slack_thread.get("channel_id")
            thread_ts = slack_thread.get("thread_ts")
            if channel_id and thread_ts:
                slack_text = _failure_text(status, dashboard_thread_url(thread_id))
                return await post_slack_thread_reply(
                    channel_id, thread_ts, slack_text, agent_thread_id=thread_id
                )
        return False

    if source == "linear":
        linear_issue = ctx.get("linear_issue")
        if isinstance(linear_issue, dict):
            issue_id = linear_issue.get("id")
            if issue_id:
                return await comment_on_linear_issue(issue_id, text)
        return False

    if source in ("github", "github_issue"):
        repo_config = metadata.get("repo")
        number = ctx.get("pr_number")
        if number is None:
            github_issue = ctx.get("github_issue")
            if isinstance(github_issue, dict):
                number = github_issue.get("number")
        if isinstance(repo_config, dict) and isinstance(number, int):
            token = await get_github_app_installation_token()
            if token:
                return await post_github_comment(repo_config, number, text, token=token)
        return False

    logger.info("No failure-reply channel for thread %s (source=%s)", thread_id, source)
    return False


def _posted_failure_run_ids(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get(_FAILURE_REPLY_RUN_IDS)
    ids = [item for item in raw if isinstance(item, str) and item] if isinstance(raw, list) else []
    latest = metadata.get(_FAILURE_REPLY_RUN_ID)
    if isinstance(latest, str) and latest and latest not in ids:
        ids.append(latest)
    return ids


def _failure_reply_metadata(metadata: dict[str, Any], run_id: str | None) -> dict[str, Any]:
    if run_id is None:
        return {_FAILURE_REPLY_FLAG: True}
    ids = [item for item in _posted_failure_run_ids(metadata) if item != run_id]
    ids.append(run_id)
    return {
        _FAILURE_REPLY_RUN_ID: run_id,
        _FAILURE_REPLY_RUN_IDS: ids[-_MAX_FAILURE_REPLY_RUN_IDS:],
    }


def _scheduled_cost_run_ids(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get(_SESSION_COST_REFRESH_RUN_IDS)
    ids = [item for item in raw if isinstance(item, str) and item] if isinstance(raw, list) else []
    latest = metadata.get(_SESSION_COST_REFRESH_RUN_ID)
    if isinstance(latest, str) and latest and latest not in ids:
        ids.append(latest)
    return ids


def _cost_refresh_metadata(metadata: dict[str, Any], run_id: str) -> dict[str, Any]:
    ids = [item for item in _scheduled_cost_run_ids(metadata) if item != run_id]
    ids.append(run_id)
    return {
        _SESSION_COST_REFRESH_RUN_ID: run_id,
        _SESSION_COST_REFRESH_RUN_IDS: ids[-_MAX_SESSION_COST_REFRESH_RUN_IDS:],
    }


def _prepare_run_id(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("metadata")
    value = metadata.get("prepare_run_id") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and value else None


async def _schedule_success_cost_refresh(
    thread_id: str, run_id: str | None, payload: dict[str, Any]
) -> dict[str, str]:
    if run_id is None:
        return {"status": "ignored", "reason": "missing run_id"}
    prepare_run_id = _prepare_run_id(payload)
    if prepare_run_id is None:
        return {"status": "ignored", "reason": "missing prepare_run_id"}

    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.warning("run-complete: could not load thread %s", thread_id, exc_info=True)
        return {"status": "error", "reason": "thread fetch failed"}
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("kind") == REVIEWER_THREAD_KIND:
        return {"status": "ignored", "reason": "not an agent Slack run"}
    if run_id in _scheduled_cost_run_ids(metadata):
        return {"status": "ignored", "reason": "cost refresh already scheduled for run"}

    source_context = metadata.get("source_context")
    slack_thread = source_context.get("slack_thread") if isinstance(source_context, dict) else None
    channel_id = slack_thread.get("channel_id") if isinstance(slack_thread, dict) else None
    thread_ts = slack_thread.get("thread_ts") if isinstance(slack_thread, dict) else None
    if not isinstance(channel_id, str) or not channel_id:
        return {"status": "ignored", "reason": "no Slack channel"}
    if not isinstance(thread_ts, str) or not thread_ts:
        return {"status": "ignored", "reason": "no Slack thread"}

    scheduled = await schedule_session_cost_refresh(
        {
            "agent_thread_id": thread_id,
            "run_id": run_id,
            "prepare_run_id": prepare_run_id,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        },
        client=client,
    )
    if not scheduled:
        return {"status": "error", "reason": "cost refresh scheduling failed"}
    try:
        await client.threads.update(
            thread_id=thread_id,
            metadata=_cost_refresh_metadata(metadata, run_id),
        )
    except Exception:  # noqa: BLE001
        logger.warning("run-complete: could not flag thread %s", thread_id, exc_info=True)
    return {"status": "ok", "reason": "cost refresh scheduled"}


async def handle_run_completion(payload: dict[str, Any]) -> dict[str, str]:
    """Handle a platform run-completion webhook POST.

    Enqueues successful Slack cost refreshes and posts failure replies idempotently.
    """
    raw_status = payload.get("status")
    status = raw_status if isinstance(raw_status, str) else ""
    thread_id = payload.get("thread_id")
    raw_run_id = payload.get("run_id")
    run_id = raw_run_id if isinstance(raw_run_id, str) and raw_run_id else None
    if not isinstance(thread_id, str) or not thread_id:
        return {"status": "ignored", "reason": "missing thread_id"}
    status_map: dict[str, RunStatus] = {
        "success": "finished",
        "interrupted": "interrupted",
        "error": "error",
        "timeout": "error",
    }
    registry_status = status_map.get(status)
    if registry_status and run_id:
        try:
            registry = await get_thread_registry()
            await registry.transition(
                thread_id,
                run_id,
                registry_status,
                environment="cloud",
                error=str(payload.get("error") or status) if registry_status == "error" else None,
            )
            if status == "success":
                state = await langgraph_client().threads.get_state(thread_id)
                values = state.get("values") if isinstance(state, dict) else None
                messages = values.get("messages") if isinstance(values, dict) else None
                await registry.append_messages(thread_id, run_id, messages_to_ui(messages))
        except (KeyError, ValueError):
            logger.debug("Completion did not match the active registry run for %s", thread_id)
        except Exception:
            logger.exception("run-complete: registry update failed for %s", thread_id)
    if status == "success":
        return await _schedule_success_cost_refresh(thread_id, run_id, payload)
    payload_metadata = payload.get("metadata")
    if (
        status in _TERMINAL_FAILURE_STATUSES
        and isinstance(payload_metadata, dict)
        and payload_metadata.get("kind") == "thread_wakeup"
    ):
        return {"status": "ignored", "reason": "automated wakeup failure"}
    if status not in _TERMINAL_FAILURE_STATUSES:
        return {"status": "ignored", "reason": f"non-failure status: {status}"}

    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.warning("run-complete: could not load thread %s", thread_id, exc_info=True)
        return {"status": "error", "reason": "thread fetch failed"}

    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    await _settle_failed_reviewer_check(thread_id, metadata)
    if run_id is None:
        # Payloads without run ids fall back to the old per-thread flag; run-scoped
        # dedupe intentionally does not read it so future runs can still report.
        if metadata.get(_FAILURE_REPLY_FLAG):
            return {"status": "ignored", "reason": "failure reply already posted"}
    elif run_id in _posted_failure_run_ids(metadata):
        return {"status": "ignored", "reason": "failure reply already posted for run"}

    posted = await _post_failure_reply(thread_id, metadata, status)
    if not posted:
        return {"status": "ignored", "reason": "no reply posted"}

    try:
        await client.threads.update(
            thread_id=thread_id,
            metadata=_failure_reply_metadata(metadata, run_id),
        )
    except Exception:  # noqa: BLE001
        logger.warning("run-complete: could not flag thread %s", thread_id, exc_info=True)
    logger.info("Posted failure reply for thread %s (status=%s)", thread_id, status)
    return {"status": "ok", "reason": "failure reply posted"}
