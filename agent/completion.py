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

from langchain_core.messages import convert_to_messages
from langgraph_sdk.client import LangGraphClient

from agent.github.app import get_github_app_installation_token
from agent.github.comments import post_github_comment
from agent.linear.client import comment_on_linear_issue
from agent.middleware.record_run_usage import finalize_agent_run_usage
from agent.review.findings import REVIEWER_THREAD_KIND
from agent.review.publish import settle_review_check_run
from agent.session_cost import schedule_session_cost_refresh
from agent.slack.client import post_slack_thread_reply
from agent.slack.code_channels import is_code_channel_session, set_session_status
from agent.source_context import SourceContext
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.errors import LAST_MODEL_ERROR_KEY, code_for_error_type
from agent.utils.thread_ops import langgraph_client
from agent.utils.user_messages import warning

logger = logging.getLogger(__name__)

# Run statuses that mean the user will otherwise get nothing back. "interrupted"
# is intentionally excluded: with multitask_strategy="interrupt", a normal
# follow-up halts the prior run (status "interrupted") while its replacement
# carries on — that's healthy, not a failure worth a "couldn't finish" reply.
_TERMINAL_FAILURE_STATUSES = frozenset({"error", "timeout"})
_TERMINAL_RUN_STATUSES = frozenset({"success", "error", "timeout", "interrupted"})
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


_REASON_TEXT = {
    "provider_overloaded": "the model provider was overloaded and never recovered",
    "provider_rate_limited": "the model provider rate-limited it",
    "provider_unavailable": "the model provider kept returning errors",
    "provider_timeout": "a model call timed out",
    "context_too_long": "the conversation outgrew the model's context window",
    "model_unavailable": "the selected model isn't available to this workspace",
    "sandbox_unreachable": "the run lost its sandbox",
    "step_limit": "the run hit its step limit",
}
_DEFAULT_FOLLOW_UP = "Send another message and it will pick this back up."
_REASON_FOLLOW_UP = {
    "context_too_long": "Start a new thread to continue.",
    "model_unavailable": "Pick a different model in Open SWE Web, then retry.",
}


def _failure_text(
    status: str, dashboard_url: str | None = None, reason_code: str | None = None
) -> str:
    reason = _REASON_TEXT.get(reason_code or "")
    if reason is None:
        if status == "timeout":
            reason = "the run timed out"
        elif status == "interrupted":
            reason = "the run was interrupted before it could finish"
        else:
            reason = "the run hit an unexpected error"
    follow_up = _REASON_FOLLOW_UP.get(reason_code or "", _DEFAULT_FOLLOW_UP)
    text = warning(f"Open SWE wasn't able to finish that — {reason}. {follow_up}")
    if dashboard_url:
        text += f" You can view the error in <{dashboard_url}|Open SWE Web>."
    return text


def _failure_reason_code(error: Any, metadata: dict[str, Any], run_id: str | None) -> str | None:
    """Classify the failure, preferring the in-run record over the class name alone.

    The recorded classification is only trusted when it names the same exception
    the run actually died with — a run can log a transient error, recover from it,
    and then fail for an unrelated reason.
    """
    error_type = error.get("error") if isinstance(error, dict) else None
    error_type = error_type if isinstance(error_type, str) else None
    recorded = metadata.get(LAST_MODEL_ERROR_KEY)
    if isinstance(recorded, dict) and recorded.get("error_type") == error_type:
        recorded_run = recorded.get("run_id")
        code = recorded.get("code")
        if isinstance(code, str) and (recorded_run is None or recorded_run == run_id):
            return code
    return code_for_error_type(error_type)


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


async def _post_failure_reply(
    thread_id: str, metadata: dict[str, Any], status: str, reason_code: str | None = None
) -> bool:
    """Post a failure reply to the run's originating channel. Best-effort."""
    source = metadata.get("source")
    ctx = SourceContext.from_metadata(metadata)
    text = _failure_text(status, reason_code=reason_code)

    if source == "slack" or ctx.slack_thread is not None:
        location = ctx.slack_location
        if location is not None:
            slack_text = _failure_text(status, dashboard_thread_url(thread_id), reason_code)
            return await post_slack_thread_reply(
                location[0], location[1], slack_text, agent_thread_id=thread_id
            )
        return False

    if source == "linear":
        if ctx.linear_issue and ctx.linear_issue.id:
            return await comment_on_linear_issue(ctx.linear_issue.id, text)
        return False

    if source in ("github", "github_issue"):
        repo_config = metadata.get("repo")
        number = ctx.pr_number
        if number is None and ctx.github_issue is not None:
            number = ctx.github_issue.number
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


async def _finalize_agent_usage_telemetry(
    thread_id: str, status: object, payload: dict[str, Any]
) -> None:
    """Finalize Agent telemetry from the platform's terminal webhook payload."""
    if status not in _TERMINAL_RUN_STATUSES:
        return
    prepare_run_id = _prepare_run_id(payload)
    if prepare_run_id is None:
        return
    values = payload.get("values")
    state = dict(values) if isinstance(values, dict) else None
    if state is not None and isinstance(state.get("messages"), list):
        try:
            state["messages"] = convert_to_messages(state["messages"])
        except (NotImplementedError, TypeError, ValueError):
            state = None
    await finalize_agent_run_usage(
        run_id=prepare_run_id,
        thread_id=thread_id,
        state=state,
    )


async def _settle_code_channel_session(
    client: LangGraphClient, thread_id: str, metadata: dict[str, Any]
) -> None:
    """Return a code channel session to ``active`` once its work stops.

    A later message can already have started another run, so a completion that
    arrives out of order must not clear the loading UI that run is relying on.
    """
    slack_thread = SourceContext.from_metadata(metadata).slack_thread
    if slack_thread is None or not is_code_channel_session(slack_thread.thread_ts):
        return
    try:
        for status in ("pending", "running"):
            if await client.runs.list(thread_id, status=status, limit=1):
                return
    except Exception:  # noqa: BLE001
        logger.debug("run-complete: could not list runs for %s", thread_id, exc_info=True)
    await set_session_status(slack_thread.channel_id, "active")


async def _schedule_success_cost_refresh(
    thread_id: str, run_id: str | None, payload: dict[str, Any]
) -> dict[str, str]:
    if run_id is None:
        return {"status": "ignored", "reason": "missing run_id"}

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
    await _settle_code_channel_session(client, thread_id, metadata)
    prepare_run_id = _prepare_run_id(payload)
    if prepare_run_id is None:
        return {"status": "ignored", "reason": "missing prepare_run_id"}
    if run_id in _scheduled_cost_run_ids(metadata):
        return {"status": "ignored", "reason": "cost refresh already scheduled for run"}

    slack_thread = SourceContext.from_metadata(metadata).slack_thread
    if slack_thread is None or not slack_thread.channel_id:
        return {"status": "ignored", "reason": "no Slack channel"}
    if not slack_thread.thread_ts:
        return {"status": "ignored", "reason": "no Slack thread"}
    channel_id = slack_thread.channel_id
    thread_ts = slack_thread.thread_ts

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
    status = payload.get("status")
    thread_id = payload.get("thread_id")
    raw_run_id = payload.get("run_id")
    run_id = raw_run_id if isinstance(raw_run_id, str) and raw_run_id else None
    if not isinstance(thread_id, str) or not thread_id:
        return {"status": "ignored", "reason": "missing thread_id"}
    await _finalize_agent_usage_telemetry(thread_id, status, payload)
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

    error = payload.get("error")
    # The platform serializes the exception (class name, and the message when its
    # type is allowlisted) — there is no traceback to attach on this side.
    error_attributes = (
        {"error": {"kind": error.get("error"), "message": error.get("message")}}
        if isinstance(error, dict)
        else {}
    )
    logger.error(
        "Run failed",
        extra={
            **error_attributes,
            "run_failure": {
                "run_id": run_id,
                "thread_id": thread_id,
                "status": status,
                "error": error,
            },
        },
    )

    client = langgraph_client()
    try:
        thread = await client.threads.get(thread_id)
    except Exception:  # noqa: BLE001
        logger.warning("run-complete: could not load thread %s", thread_id, exc_info=True)
        return {"status": "error", "reason": "thread fetch failed"}

    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    await _settle_failed_reviewer_check(thread_id, metadata)
    await _settle_code_channel_session(client, thread_id, metadata)
    if run_id is None:
        # Payloads without run ids fall back to the old per-thread flag; run-scoped
        # dedupe intentionally does not read it so future runs can still report.
        if metadata.get(_FAILURE_REPLY_FLAG):
            return {"status": "ignored", "reason": "failure reply already posted"}
    elif run_id in _posted_failure_run_ids(metadata):
        return {"status": "ignored", "reason": "failure reply already posted for run"}

    reason_code = _failure_reason_code(error, metadata, run_id)
    posted = await _post_failure_reply(thread_id, metadata, status, reason_code)
    if not posted:
        return {"status": "ignored", "reason": "no reply posted"}

    try:
        await client.threads.update(
            thread_id=thread_id,
            metadata=_failure_reply_metadata(metadata, run_id),
        )
    except Exception:  # noqa: BLE001
        logger.warning("run-complete: could not flag thread %s", thread_id, exc_info=True)
    logger.info(
        "Posted failure reply",
        extra={"failure_reply": {"thread_id": thread_id, "status": status, "code": reason_code}},
    )
    return {"status": "ok", "reason": "failure reply posted"}
