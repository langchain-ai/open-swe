"""Linear webhook HTTP routes."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import ValidationError

from agent.linear import sessions
from agent.linear.deliveries import claim_delivery, is_fresh
from agent.linear.schema import (
    AgentSessionEvent,
    CommentCreateEvent,
    parse_linear_webhook,
)
from agent.linear.webhook import process_linear_issue
from agent.webhooks.common import (
    LINEAR_WEBHOOK_SECRET,
    _is_repo_allowed,
    describe_open_swe_tags,
    mentions_open_swe,
    verify_linear_signature,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _ignored(reason: str) -> dict[str, str]:
    logger.debug("Ignoring a Linear webhook", extra={"linear_ignore_reason": reason})
    return {"status": "ignored", "reason": reason}


def _accept_session_event(
    event: AgentSessionEvent, background_tasks: BackgroundTasks
) -> dict[str, str]:
    handler = sessions.start_session if event.action == "created" else sessions.continue_session
    background_tasks.add_task(handler, event)
    logger.info(
        "Accepted a Linear agent session event",
        extra={
            "linear_session_id": event.agent_session.id,
            "linear_session_action": event.action,
        },
    )
    return {"status": "accepted"}


async def _accept_comment_event(
    event: CommentCreateEvent, background_tasks: BackgroundTasks
) -> dict[str, str]:
    if event.action != "create":
        return _ignored(f"comment action is '{event.action}'")

    comment = event.data
    if comment.bot_actor:
        return _ignored("comment is from a bot")
    if await sessions.is_own_comment(comment):
        return _ignored("comment is our own")
    if not mentions_open_swe(comment.body):
        return _ignored(f"comment doesn't mention {describe_open_swe_tags()}")
    if comment.issue is None:
        return _ignored("no issue data in comment")

    issue = await sessions.load_issue(comment.issue)
    repo_config = await sessions.resolve_repo_config(
        trigger_text=comment.body,
        requester_email=(comment.user.email or "") if comment.user is not None else "",
        issue=issue,
    )
    if not repo_config:
        return _ignored("no default repository configured")
    if not _is_repo_allowed(repo_config):
        logger.warning(
            "Rejecting a Linear comment for a repository outside the allowlist",
            extra={"linear_repo": f"{repo_config['owner']}/{repo_config['name']}"},
        )
        return _ignored("repository not in allowlist")

    background_tasks.add_task(process_linear_issue, issue, repo_config, trigger=comment)
    logger.info(
        "Accepted a Linear comment trigger",
        extra={"linear_issue_id": issue.id, "linear_comment_id": comment.id},
    )
    return {"status": "accepted"}


@router.post("/webhooks/linear")
async def linear_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Authenticate a Linear webhook and hand its work to a background task."""
    body = await request.body()
    if not verify_linear_signature(
        body, request.headers.get("Linear-Signature", ""), LINEAR_WEBHOOK_SECRET
    ):
        logger.warning("Rejecting a Linear webhook with an invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    delivery_id = request.headers.get("Linear-Delivery", "")
    if not delivery_id:
        logger.warning("Rejecting a Linear webhook with no delivery id")
        raise HTTPException(status_code=401, detail="Missing Linear-Delivery header")

    try:
        event = parse_linear_webhook(body)
    except ValidationError:
        logger.warning("Rejecting an unparseable Linear webhook", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid payload") from None

    if not is_fresh(event.webhook_timestamp):
        logger.warning(
            "Rejecting a stale Linear webhook", extra={"linear_delivery_id": delivery_id}
        )
        raise HTTPException(status_code=401, detail="Stale delivery")

    if not await claim_delivery(delivery_id):
        return _ignored("duplicate delivery")

    if isinstance(event, AgentSessionEvent):
        return _accept_session_event(event, background_tasks)
    if isinstance(event, CommentCreateEvent):
        return await _accept_comment_event(event, background_tasks)
    return _ignored(f"unsupported event type '{event.type}'")


@router.get("/webhooks/linear")
async def linear_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Linear webhook setup."""
    return {"status": "ok", "message": "Linear webhook endpoint is active"}
