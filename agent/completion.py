"""Run-completion webhook handler — guarantees every run ends with a signal.

The platform POSTs a run-completion payload to ``/webhooks/run-complete`` (wired
as the ``webhook`` on every dispatched run, see ``agent.dispatch``). When a run
ends in a failure state (``error`` / ``timeout`` / ``interrupted``) we post a
short failure reply to the originating channel, so a run that died on a server
recycle or hit a limit never leaves the user in silence.

This decouples "the user gets an answer" from "the agent remembered to reply."
The reply is idempotent: a per-thread metadata flag prevents double-posting when
the platform retries the webhook or a checkpoint replays.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

from .utils.github_app import get_github_app_installation_token
from .utils.github_comments import post_github_comment
from .utils.linear import comment_on_linear_issue
from .utils.slack import post_slack_thread_reply
from .utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

# Run statuses that mean the user will otherwise get nothing back. "interrupted"
# is intentionally excluded: with multitask_strategy="interrupt", a normal
# follow-up halts the prior run (status "interrupted") while its replacement
# carries on — that's healthy, not a failure worth a "couldn't finish" reply.
_TERMINAL_FAILURE_STATUSES = frozenset({"error", "timeout"})
_FAILURE_REPLY_FLAG = "failure_reply_posted"

# Shared-secret bearer token proving a /webhooks/run-complete call came from our
# own dispatch (which appends ?token= when this is set) rather than from an
# attacker hitting the public route. When unset (dev), verification is skipped.
RUN_COMPLETE_WEBHOOK_SECRET = os.environ.get("RUN_COMPLETE_WEBHOOK_SECRET")


def verify_run_complete_token(token: str | None) -> bool:
    """Return whether a run-completion webhook token is acceptable."""
    secret = RUN_COMPLETE_WEBHOOK_SECRET
    if not secret:
        return True
    return token is not None and hmac.compare_digest(token, secret)


def _failure_text(status: str) -> str:
    if status == "timeout":
        reason = "timed out"
    elif status == "interrupted":
        reason = "was interrupted before it could finish"
    else:
        reason = "hit an unexpected error"
    return (
        f"⚠️ I wasn't able to finish that — the run {reason}. "
        "Send another message and I'll pick it back up."
    )


async def _post_failure_reply(thread_id: str, metadata: dict[str, Any], status: str) -> bool:
    """Post a failure reply to the run's originating channel. Best-effort."""
    source = metadata.get("source")
    ctx = metadata.get("source_context")
    ctx = ctx if isinstance(ctx, dict) else {}
    text = _failure_text(status)

    if source == "slack":
        slack_thread = ctx.get("slack_thread")
        if isinstance(slack_thread, dict):
            channel_id = slack_thread.get("channel_id")
            thread_ts = slack_thread.get("thread_ts")
            if channel_id and thread_ts:
                return await post_slack_thread_reply(channel_id, thread_ts, text)
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


async def handle_run_completion(payload: dict[str, Any]) -> dict[str, str]:
    """Handle a platform run-completion webhook POST.

    Posts a failure reply only when the run ended in a failure state and we
    haven't already replied for this thread.
    """
    status = payload.get("status")
    thread_id = payload.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return {"status": "ignored", "reason": "missing thread_id"}
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
    if metadata.get(_FAILURE_REPLY_FLAG):
        return {"status": "ignored", "reason": "failure reply already posted"}

    posted = await _post_failure_reply(thread_id, metadata, status)
    if not posted:
        return {"status": "ignored", "reason": "no reply posted"}

    try:
        await client.threads.update(thread_id=thread_id, metadata={_FAILURE_REPLY_FLAG: True})
    except Exception:  # noqa: BLE001
        logger.warning("run-complete: could not flag thread %s", thread_id, exc_info=True)
    logger.info("Posted failure reply for thread %s (status=%s)", thread_id, status)
    return {"status": "ok", "reason": "failure reply posted"}
