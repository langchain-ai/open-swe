"""Linear webhook HTTP routes."""

import re

from fastapi import APIRouter

from agent.linear import webhook as service
from agent.webhooks import common

router = APIRouter()

_ISSUE_IDENTIFIER_RE = re.compile(r"/issue/([A-Za-z][A-Za-z0-9_]*-\d+)(?:/|$)")


def _issue_url(payload_url: object) -> tuple[str, str]:
    if not isinstance(payload_url, str):
        return "", ""
    base_url = payload_url.split("#", 1)[0]
    match = _ISSUE_IDENTIFIER_RE.search(base_url)
    return base_url, match.group(1) if match else ""


@router.post("/webhooks/linear")
async def linear_webhook(  # noqa: PLR0911, PLR0912, PLR0915
    request: common.Request, background_tasks: common.BackgroundTasks
) -> dict[str, str]:
    """Handle Linear webhooks.

    Triggers a new LangGraph run when an issue gets the 'open-swe' label added.
    """
    common.logger.info("Received Linear webhook")
    body = await request.body()

    signature = request.headers.get("Linear-Signature", "")
    if not common.verify_linear_signature(body, signature, common.LINEAR_WEBHOOK_SECRET):
        common.logger.warning("Invalid webhook signature")
        raise common.HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = common.json.loads(body)
    except common.json.JSONDecodeError:
        common.logger.exception("Failed to parse webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") != "Comment":
        common.logger.debug("Ignoring webhook: not a Comment event")
        return {"status": "ignored", "reason": "Not a Comment event"}

    action = payload.get("action")
    if action != "create":
        common.logger.debug("Ignoring webhook: action is %s, not create", action)
        return {
            "status": "ignored",
            "reason": f"Comment action is '{action}', only processing 'create'",
        }

    data = payload.get("data", {})

    if data.get("botActor"):
        common.logger.debug("Ignoring webhook: comment is from a bot")
        return {"status": "ignored", "reason": "Comment is from a bot"}

    comment_body = data.get("body", "")
    bot_message_prefixes = [
        "🔐 **GitHub Authentication Required**",
        "✅ **Pull Request Created**",
        "✅ **Pull Request Updated**",
        "**Pull Request Created**",
        "**Pull Request Updated**",
        "🤖 **Agent Response**",
        "❌ **Agent Error**",
    ]
    for prefix in bot_message_prefixes:
        if comment_body.startswith(prefix):
            common.logger.debug("Ignoring webhook: comment is our own bot message")
            return {"status": "ignored", "reason": "Comment is our own bot message"}
    if not common.mentions_open_swe(comment_body):
        tags = common.describe_open_swe_tags()
        common.logger.debug("Ignoring webhook: comment doesn't mention %s", tags)
        return {"status": "ignored", "reason": f"Comment doesn't mention {tags}"}

    nested_issue = data.get("issue")
    issue = dict(nested_issue) if isinstance(nested_issue, dict) else {}
    issue_id = issue.get("id") or data.get("issueId")
    if not isinstance(issue_id, str) or not issue_id:
        common.logger.debug("Ignoring webhook: no issue id in comment")
        return {"status": "ignored", "reason": "No issue id in comment"}
    issue_url, identifier = _issue_url(payload.get("url"))
    issue["id"] = issue_id
    issue.setdefault("title", identifier or "Linear issue")
    issue.setdefault("identifier", identifier)
    issue.setdefault("url", issue_url)

    repo_config = common.extract_repo_from_text(
        comment_body, default_owner=common.DEFAULT_REPO_OWNER
    )

    if repo_config:
        common.logger.debug(
            "Using repo from comment body: %s/%s",
            repo_config["owner"],
            repo_config["name"],
        )
    else:
        comment_user_email = (data.get("user") or payload.get("actor") or {}).get("email")
        try:
            profile_repo = await common.get_profile_default_repo(
                await common.resolve_login_from_email_async(comment_user_email)
            )
        except Exception:  # noqa: BLE001
            common.logger.exception("Failed to apply dashboard default_repo for Linear user")
            profile_repo = None
        if profile_repo:
            common.logger.info(
                "Applying dashboard default_repo for Linear user %s: %s/%s",
                comment_user_email,
                profile_repo["owner"],
                profile_repo["name"],
            )
            repo_config = profile_repo

    if not repo_config:
        repo_config = await common.get_team_default_repo()

    if not repo_config:
        return {"status": "ignored", "reason": "No default repository configured"}

    if not common.is_repo_allowed(repo_config):
        common.logger.warning(
            "Rejecting Linear webhook: repo '%s/%s' not in allowlist",
            repo_config.get("owner"),
            repo_config.get("name"),
        )
        return {"status": "ignored", "reason": "Repository not in allowlist"}

    repo_owner = repo_config["owner"]
    repo_name = repo_config["name"]

    issue["triggering_comment"] = comment_body
    issue["triggering_comment_id"] = data.get("id", "")
    comment_user = data.get("user") or payload.get("actor") or {}
    if comment_user:
        issue["comment_author"] = comment_user

    common.logger.info(
        "Accepted webhook for issue '%s' (%s), scheduling background task",
        issue.get("title"),
        issue.get("id"),
    )
    background_tasks.add_task(service.process_linear_issue, issue, repo_config)

    return {
        "status": "accepted",
        "message": f"Processing issue '{issue.get('title')}' for repo {repo_owner}/{repo_name}",
    }


@router.get("/webhooks/linear")
async def linear_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Linear webhook setup."""
    return {"status": "ok", "message": "Linear webhook endpoint is active"}
