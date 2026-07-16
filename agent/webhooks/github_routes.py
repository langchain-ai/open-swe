"""Github webhook HTTP routes."""

from fastapi import APIRouter

from . import common
from . import github as service

router = APIRouter()


@router.post("/webhooks/github")
async def github_webhook(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> dict[str, str]:
    """Handle GitHub webhooks for issue and PR events that tag @open-swe."""
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not common.verify_github_signature(body, signature, secret=common.GITHUB_WEBHOOK_SECRET):
        common.logger.warning("Invalid GitHub webhook signature")
        raise common.HTTPException(status_code=401, detail="Invalid signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type not in common._SUPPORTED_GH_EVENTS:
        common.logger.info("Ignoring unsupported GitHub event type: %s", event_type)
        return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}

    try:
        payload = common.json.loads(body)
    except common.json.JSONDecodeError:
        common.logger.exception("Failed to parse GitHub webhook JSON")
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
        if action not in common._SUPPORTED_GH_PULL_REQUEST_ACTIONS:
            common.logger.info("Ignoring unsupported GitHub pull_request action: %s", action)
            return {
                "status": "ignored",
                "reason": f"Unsupported GitHub pull_request action: {action}",
            }
        if action in common._GH_PR_AGENT_STATE_ACTIONS:
            background_tasks.add_task(common.update_agent_thread_pr_state, payload)
        if action in common._GH_PR_WATCH_TOGGLE_ACTIONS:
            common.logger.info(
                "Accepted GitHub PR %s webhook, scheduling reviewer watch update", action
            )
            background_tasks.add_task(service.process_github_pr_close, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for reviewer watch"}
        if action in common._GH_PR_FIRST_REVIEW_ACTIONS:
            if not await common._is_repo_auto_review_enabled(webhook_repo_config):
                return {"status": "ignored", "reason": "Automatic review disabled for repository"}
            gate_rejection = await common._enforce_public_repo_org_gate(payload, "pull_request")
            if gate_rejection is not None:
                return gate_rejection
            common.logger.info("Accepted GitHub PR %s webhook, scheduling auto-review task", action)
            background_tasks.add_task(service.process_github_pr_ready, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for auto-review"}
        common.logger.info("Ignoring unsupported GitHub pull_request action: %s", action)
        return {
            "status": "ignored",
            "reason": f"Unsupported GitHub pull_request action: {action}",
        }

    if event_type == "push":
        if not await common._is_repo_auto_review_enabled(webhook_repo_config):
            return {"status": "ignored", "reason": "Automatic review disabled for repository"}
        common.logger.info("Accepted GitHub push webhook, scheduling reviewer watch evaluation")
        background_tasks.add_task(service.process_github_push_event, payload)
        return {"status": "accepted", "message": "Processing GitHub push for reviewer watch"}

    if not common._is_repo_allowed(webhook_repo_config):
        common.logger.debug(
            "Rejecting GitHub webhook: repo '%s/%s' not in allowlist",
            webhook_repo_config.get("owner"),
            webhook_repo_config.get("name"),
        )
        return {"status": "ignored", "reason": "Repository not in allowlist"}

    if is_issue_event:
        action = payload.get("action", "")
        if action not in common._SUPPORTED_GH_ISSUE_ACTIONS:
            common.logger.info("Ignoring unsupported GitHub issue action: %s", action)
            return {"status": "ignored", "reason": f"Unsupported GitHub issue action: {action}"}
        if action == "edited":
            changes = payload.get("changes", {})
            if not any(field in changes for field in ("body", "title")):
                common.logger.info("Ignoring GitHub issue edit without title/body changes")
                return {"status": "ignored", "reason": "Issue edit did not change title or body"}

        issue_text = f"{issue.get('title', '')}\n\n{issue.get('body', '')}".lower()
        if not any(tag in issue_text for tag in common.OPEN_SWE_TAGS):
            common.logger.info("Ignoring issue that does not mention @openswe or @open-swe")
            return {"status": "ignored", "reason": "Issue does not mention @openswe or @open-swe"}

        gate_rejection = await common._enforce_public_repo_org_gate(payload, event_type)
        if gate_rejection is not None:
            return gate_rejection

        common.logger.info("Accepted GitHub issue webhook, scheduling background task")
        background_tasks.add_task(service.process_github_issue, payload, event_type)
        return {"status": "accepted", "message": "Processing GitHub issue event"}

    action = payload.get("action", "")
    supported_comment_actions = common._SUPPORTED_GH_COMMENT_ACTIONS.get(event_type)
    if supported_comment_actions is None:
        common.logger.info("Ignoring unsupported GitHub payload shape for event=%s", event_type)
        return {"status": "ignored", "reason": f"Unsupported payload for event type: {event_type}"}
    if action and action not in supported_comment_actions:
        common.logger.debug("Ignoring unsupported GitHub %s action: %s", event_type, action)
        return {"status": "ignored", "reason": f"Unsupported GitHub {event_type} action: {action}"}

    comment = payload.get("comment") or payload.get("review", {})
    comment_body = (comment.get("body") or "") if comment else ""

    if (
        event_type == "pull_request_review_comment"
        and common._review_comment_reply_parent_id(payload) is not None
    ):
        gate_rejection = await common._enforce_public_repo_org_gate(payload, event_type)
        if gate_rejection is not None:
            return gate_rejection
        background_tasks.add_task(service.process_github_review_finding_reply, payload)
        return {"status": "accepted", "message": "Processing review finding reply"}

    if not any(tag in comment_body.lower() for tag in common.OPEN_SWE_TAGS):
        common.logger.debug(
            "Ignoring GitHub %s%s that does not mention @openswe or @open-swe",
            event_type,
            f" action={action}" if action else "",
        )
        return {"status": "ignored", "reason": "Comment does not mention @openswe or @open-swe"}

    gate_rejection = await common._enforce_public_repo_org_gate(payload, event_type)
    if gate_rejection is not None:
        return gate_rejection

    if (
        is_pull_request_comment
        or event_type in {"pull_request_review_comment", "pull_request_review"}
    ) and service.is_stackability_directive(comment_body):
        pull_request = payload.get("pull_request") or issue
        pr_number = pull_request.get("number") or issue.get("number")
        pr_url = pull_request.get("html_url") or issue.get("html_url", "")
        if isinstance(pr_number, int):
            pr_ref = common.GitHubPrRef(
                owner=webhook_repo_config.get("owner", ""),
                repo=webhook_repo_config.get("name", ""),
                number=pr_number,
                url=pr_url,
            )
            sender = payload.get("sender") or {}
            background_tasks.add_task(
                service.trigger_pr_review_from_ref,
                pr_ref,
                source="github_stackability",
                github_login=sender.get("login", ""),
                github_user_id=sender.get("id"),
                review_mode="stackability",
            )
            return {"status": "accepted", "message": "Processing stackability review request"}

    common.logger.info("Accepted GitHub webhook: event=%s, scheduling background task", event_type)
    if is_pull_request_comment or event_type in {
        "pull_request_review_comment",
        "pull_request_review",
    }:
        background_tasks.add_task(service.process_github_pr_comment, payload, event_type)
        return {"status": "accepted", "message": f"Processing {event_type} event"}

    if is_issue_comment:
        background_tasks.add_task(service.process_github_issue, payload, event_type)
        return {"status": "accepted", "message": "Processing GitHub issue comment event"}

    common.logger.info("Ignoring unsupported GitHub payload shape for event=%s", event_type)
    return {"status": "ignored", "reason": f"Unsupported payload for event type: {event_type}"}
