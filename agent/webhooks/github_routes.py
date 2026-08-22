"""GitHub webhook HTTP routes: verify, filter, and schedule the handler."""

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..config import github_webhook_secret, public_repo_org_gate
from ..github.comments import describe_open_swe_tags, mentions_open_swe
from ..github.org_membership import (
    internal_bot_logins,
    is_repo_allowed,
    is_user_active_org_member,
)
from ..review.dispatch import auto_review_enabled
from ..settings.agent_usage import update_agent_pr_usage_from_webhook
from . import github as service
from .signatures import verify_github_signature

logger = logging.getLogger(__name__)

router = APIRouter()

_GITHUB_CI_EVENTS = frozenset(["check_run", "check_suite", "workflow_run", "status"])
_SUPPORTED_GH_EVENTS = frozenset(
    [
        "issue_comment",
        "issues",
        "pull_request",
        "pull_request_review_comment",
        "pull_request_review",
        "push",
        *_GITHUB_CI_EVENTS,
    ]
)
_SUPPORTED_GH_ISSUE_ACTIONS = frozenset(["edited", "opened", "reopened"])
_SUPPORTED_GH_PULL_REQUEST_ACTIONS = frozenset(
    [
        "opened",
        "ready_for_review",
        "converted_to_draft",
        "closed",
        "reopened",
        "synchronize",
    ]
)
_GH_PR_WATCH_TOGGLE_ACTIONS = frozenset(["closed", "reopened", "converted_to_draft"])
_GH_PR_FIRST_REVIEW_ACTIONS = frozenset(["opened", "ready_for_review"])
# PR lifecycle actions that should refresh the agent thread's tracked pr_state.
_GH_PR_AGENT_STATE_ACTIONS = frozenset(
    ["closed", "reopened", "converted_to_draft", "ready_for_review", "synchronize"]
)
_SUPPORTED_GH_COMMENT_ACTIONS = {
    "issue_comment": frozenset(["created", "edited"]),
    "pull_request_review_comment": frozenset(["created", "edited"]),
    "pull_request_review": frozenset(["submitted", "edited"]),
}

_PUBLIC_REPO_GATE_REJECTION = {
    "status": "ignored",
    "reason": "Sender is not a member of the allowed organization for public-repo triggers",
}


async def _is_sender_allowed_for_public_repo(payload: dict[str, Any]) -> bool:
    """Public-repo gate: only ``PUBLIC_REPO_ORG_GATE`` org members may trigger.

    Returns True (allowed) when:
    - The gate is disabled (``PUBLIC_REPO_ORG_GATE`` empty), OR
    - The repo is private (gate only applies to public repos), OR
    - The sender is a known internal bot, OR
    - The sender is an active member of ``PUBLIC_REPO_ORG_GATE``.
    """
    gate = public_repo_org_gate()
    if not gate:
        return True

    repository = payload.get("repository") or {}
    if repository.get("private", False):
        return True

    sender = payload.get("sender") or {}
    sender_login = sender.get("login", "") or ""
    if sender_login in internal_bot_logins():
        return True

    if not sender_login:
        return False

    return await is_user_active_org_member(sender_login, gate)


async def _enforce_public_repo_org_gate(
    payload: dict[str, Any], event_type: str
) -> dict[str, str] | None:
    """Return a rejection response if the public-repo org gate blocks this event."""
    if await _is_sender_allowed_for_public_repo(payload):
        return None
    sender_login = (payload.get("sender") or {}).get("login", "")
    repo = payload.get("repository") or {}
    logger.warning(
        "Blocking GitHub %s from non-org-member sender '%s' on public repo '%s/%s'",
        event_type,
        sender_login,
        (repo.get("owner") or {}).get("login", ""),
        repo.get("name", ""),
    )
    return _PUBLIC_REPO_GATE_REJECTION


@router.post("/webhooks/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Handle GitHub webhooks for issue and PR events that tag @open-swe."""
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_github_signature(body, signature, secret=github_webhook_secret()):
        logger.warning("Invalid GitHub webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type not in _SUPPORTED_GH_EVENTS:
        logger.info("Ignoring unsupported GitHub event type: %s", event_type)
        return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse GitHub webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    webhook_repo = payload.get("repository", {})
    webhook_repo_config = {
        "owner": webhook_repo.get("owner", {}).get("login", ""),
        "name": webhook_repo.get("name", ""),
    }

    issue = payload.get("issue", {})
    is_pull_request_comment = bool(event_type == "issue_comment" and issue.get("pull_request"))
    is_issue_comment = bool(event_type == "issue_comment" and not issue.get("pull_request"))
    is_issue_event = event_type == "issues"
    is_pull_request_event = event_type == "pull_request"

    if is_pull_request_event:
        action = payload.get("action", "")
        if action not in _SUPPORTED_GH_PULL_REQUEST_ACTIONS:
            logger.info("Ignoring unsupported GitHub pull_request action: %s", action)
            return {
                "status": "ignored",
                "reason": f"Unsupported GitHub pull_request action: {action}",
            }
        if action in _GH_PR_AGENT_STATE_ACTIONS:
            background_tasks.add_task(service.update_agent_thread_pr_state, payload)
            try:
                await update_agent_pr_usage_from_webhook(payload)
            except Exception:  # noqa: BLE001
                # Telemetry only; the webhook is still acknowledged and acted on.
                logger.debug("Failed to update Agent PR usage", exc_info=True)
        if action in _GH_PR_WATCH_TOGGLE_ACTIONS:
            logger.info("Accepted GitHub PR %s webhook, scheduling reviewer watch update", action)
            background_tasks.add_task(service.process_github_pr_close, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for reviewer watch"}
        if action in _GH_PR_FIRST_REVIEW_ACTIONS:
            if not await auto_review_enabled(webhook_repo_config):
                return {"status": "ignored", "reason": "Automatic review disabled for repository"}
            gate_rejection = await _enforce_public_repo_org_gate(payload, "pull_request")
            if gate_rejection is not None:
                return gate_rejection
            logger.info("Accepted GitHub PR %s webhook, scheduling auto-review task", action)
            background_tasks.add_task(service.process_github_pr_ready, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for auto-review"}
        if action in _GH_PR_AGENT_STATE_ACTIONS:
            return {"status": "accepted", "message": f"Processing PR {action} state"}
        logger.info("Ignoring unsupported GitHub pull_request action: %s", action)
        return {
            "status": "ignored",
            "reason": f"Unsupported GitHub pull_request action: {action}",
        }

    if event_type == "push":
        if not await auto_review_enabled(webhook_repo_config):
            return {"status": "ignored", "reason": "Automatic review disabled for repository"}
        logger.info("Accepted GitHub push webhook, scheduling reviewer watch evaluation")
        background_tasks.add_task(service.process_github_push_event, payload)
        return {"status": "accepted", "message": "Processing GitHub push for reviewer watch"}

    if not is_repo_allowed(webhook_repo_config):
        logger.debug(
            "Rejecting GitHub webhook: repo '%s/%s' not in allowlist",
            webhook_repo_config.get("owner"),
            webhook_repo_config.get("name"),
        )
        return {"status": "ignored", "reason": "Repository not in allowlist"}

    if event_type in _GITHUB_CI_EVENTS:
        delivery_id = request.headers.get("X-GitHub-Delivery")
        background_tasks.add_task(
            service.process_github_ci_event,
            payload,
            event_type,
            delivery_id,
        )
        return {"status": "accepted", "message": "Processing GitHub CI event"}

    if is_issue_event:
        action = payload.get("action", "")
        if action not in _SUPPORTED_GH_ISSUE_ACTIONS:
            logger.info("Ignoring unsupported GitHub issue action: %s", action)
            return {"status": "ignored", "reason": f"Unsupported GitHub issue action: {action}"}
        if action == "edited":
            changes = payload.get("changes", {})
            if not any(field in changes for field in ("body", "title")):
                logger.info("Ignoring GitHub issue edit without title/body changes")
                return {"status": "ignored", "reason": "Issue edit did not change title or body"}

        issue_text = f"{issue.get('title', '')}\n\n{issue.get('body', '')}"
        if not mentions_open_swe(issue_text):
            tags = describe_open_swe_tags()
            logger.info("Ignoring issue that does not mention %s", tags)
            return {"status": "ignored", "reason": f"Issue does not mention {tags}"}

        gate_rejection = await _enforce_public_repo_org_gate(payload, event_type)
        if gate_rejection is not None:
            return gate_rejection

        logger.info("Accepted GitHub issue webhook, scheduling background task")
        background_tasks.add_task(service.process_github_issue, payload, event_type)
        return {"status": "accepted", "message": "Processing GitHub issue event"}

    action = payload.get("action", "")
    supported_comment_actions = _SUPPORTED_GH_COMMENT_ACTIONS.get(event_type)
    if supported_comment_actions is None:
        logger.info("Ignoring unsupported GitHub payload shape for event=%s", event_type)
        return {"status": "ignored", "reason": f"Unsupported payload for event type: {event_type}"}
    if action and action not in supported_comment_actions:
        logger.debug("Ignoring unsupported GitHub %s action: %s", event_type, action)
        return {"status": "ignored", "reason": f"Unsupported GitHub {event_type} action: {action}"}

    comment = payload.get("comment") or payload.get("review", {})
    comment_body = (comment.get("body") or "") if comment else ""

    if (
        event_type == "pull_request_review_comment"
        and service.review_comment_reply_parent_id(payload) is not None
    ):
        gate_rejection = await _enforce_public_repo_org_gate(payload, event_type)
        if gate_rejection is not None:
            return gate_rejection
        background_tasks.add_task(service.process_github_review_finding_reply, payload)
        return {"status": "accepted", "message": "Processing review finding reply"}

    if not mentions_open_swe(comment_body):
        tags = describe_open_swe_tags()
        logger.debug(
            "Ignoring GitHub %s%s that does not mention %s",
            event_type,
            f" action={action}" if action else "",
            tags,
        )
        return {"status": "ignored", "reason": f"Comment does not mention {tags}"}

    gate_rejection = await _enforce_public_repo_org_gate(payload, event_type)
    if gate_rejection is not None:
        return gate_rejection

    logger.info("Accepted GitHub webhook: event=%s, scheduling background task", event_type)
    if is_pull_request_comment or event_type in {
        "pull_request_review_comment",
        "pull_request_review",
    }:
        background_tasks.add_task(service.process_github_pr_comment, payload, event_type)
        return {"status": "accepted", "message": f"Processing {event_type} event"}

    if is_issue_comment:
        background_tasks.add_task(service.process_github_issue, payload, event_type)
        return {"status": "accepted", "message": "Processing GitHub issue comment event"}

    logger.info("Ignoring unsupported GitHub payload shape for event=%s", event_type)
    return {"status": "ignored", "reason": f"Unsupported payload for event type: {event_type}"}
