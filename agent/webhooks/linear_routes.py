"""Linear webhook HTTP routes."""

from fastapi import APIRouter

from . import common
from . import linear as service

router = APIRouter()


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
    if not any(tag in comment_body.lower() for tag in common.OPEN_SWE_TAGS):
        common.logger.debug("Ignoring webhook: comment doesn't mention %s", common.OPEN_SWE_TAGS)
        return {"status": "ignored", "reason": f"Comment doesn't mention {common.OPEN_SWE_TAGS}"}

    issue = data.get("issue", {})
    if not issue:
        common.logger.debug("Ignoring webhook: no issue data in comment")
        return {"status": "ignored", "reason": "No issue data in comment"}

    # Fetch full issue details to get project info (webhook doesn't include it)
    issue_id = issue.get("id", "")
    full_issue = await common.fetch_linear_issue_details(issue_id)
    if not full_issue:
        common.logger.warning("Failed to fetch full issue details, using webhook data")
        full_issue = issue

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
        comment_user_email = (data.get("user") or {}).get("email")
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
        team = full_issue.get("team", {})
        team_name = team.get("name", "") if team else ""
        project = full_issue.get("project")
        project_name = project.get("name", "") if project else ""

        team_identifier = team_name.strip() if team_name else ""
        project_key = project_name.strip() if project_name else ""

        repo_config = common.get_repo_config_from_team_mapping(team_identifier, project_key)

        common.logger.debug(
            "Team/project lookup result",
            extra={
                "team_name": team_identifier,
                "project_name": project_key,
                "repo_config": repo_config,
            },
        )

    if not repo_config:
        repo_config = await common.get_team_default_repo()

    if not repo_config:
        return {"status": "ignored", "reason": "No default repository configured"}

    if not common._is_repo_allowed(repo_config):
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
    comment_user = data.get("user", {})
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
