"""Linear webhook HTTP routes."""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..config import linear_webhook_secret
from ..github.comments import describe_open_swe_tags, mentions_open_swe
from ..github.org_membership import is_repo_allowed
from ..utils.linear import fetch_issue_details
from . import linear as service
from .signatures import verify_linear_signature

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/webhooks/linear")
async def linear_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Handle Linear webhooks.

    Triggers a new LangGraph run when a comment on an issue mentions Open SWE.
    """
    logger.info("Received Linear webhook")
    body = await request.body()

    signature = request.headers.get("Linear-Signature", "")
    if not verify_linear_signature(body, signature, linear_webhook_secret()):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.exception("Failed to parse webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") != "Comment":
        logger.debug("Ignoring webhook: not a Comment event")
        return {"status": "ignored", "reason": "Not a Comment event"}

    action = payload.get("action")
    if action != "create":
        logger.debug("Ignoring webhook: action is %s, not create", action)
        return {
            "status": "ignored",
            "reason": f"Comment action is '{action}', only processing 'create'",
        }

    data = payload.get("data", {})

    if data.get("botActor"):
        logger.debug("Ignoring webhook: comment is from a bot")
        return {"status": "ignored", "reason": "Comment is from a bot"}

    comment_body = data.get("body", "")
    if any(comment_body.startswith(prefix) for prefix in service.BOT_MESSAGE_PREFIXES):
        logger.debug("Ignoring webhook: comment is our own bot message")
        return {"status": "ignored", "reason": "Comment is our own bot message"}
    if not mentions_open_swe(comment_body):
        tags = describe_open_swe_tags()
        logger.debug("Ignoring webhook: comment doesn't mention %s", tags)
        return {"status": "ignored", "reason": f"Comment doesn't mention {tags}"}

    issue = data.get("issue", {})
    if not issue:
        logger.debug("Ignoring webhook: no issue data in comment")
        return {"status": "ignored", "reason": "No issue data in comment"}

    # Fetch full issue details to get project info (webhook doesn't include it)
    full_issue = await fetch_issue_details(issue.get("id", ""))
    if not full_issue:
        logger.warning("Failed to fetch full issue details, using webhook data")
        full_issue = issue

    repo_config = await service.get_linear_repo_config(
        comment_body,
        comment_user_email=(data.get("user") or {}).get("email"),
        issue=full_issue,
    )
    if not repo_config:
        return {"status": "ignored", "reason": "No default repository configured"}

    if not is_repo_allowed(repo_config):
        logger.warning(
            "Rejecting Linear webhook: repo '%s/%s' not in allowlist",
            repo_config.get("owner"),
            repo_config.get("name"),
        )
        return {"status": "ignored", "reason": "Repository not in allowlist"}

    issue["triggering_comment"] = comment_body
    issue["triggering_comment_id"] = data.get("id", "")
    comment_user = data.get("user", {})
    if comment_user:
        issue["comment_author"] = comment_user

    logger.info(
        "Accepted webhook for issue '%s' (%s), scheduling background task",
        issue.get("title"),
        issue.get("id"),
    )
    background_tasks.add_task(service.process_linear_issue, issue, repo_config)

    return {
        "status": "accepted",
        "message": (
            f"Processing issue '{issue.get('title')}' for repo "
            f"{repo_config['owner']}/{repo_config['name']}"
        ),
    }


@router.get("/webhooks/linear")
async def linear_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Linear webhook setup."""
    return {"status": "ok", "message": "Linear webhook endpoint is active"}
