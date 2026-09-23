"""Github webhook HTTP routes."""

from fastapi import APIRouter, Response

from agent.expedited_review.watch import WATCHED_GITHUB_EVENTS as EXPEDITED_REVIEW_EVENTS
from agent.github import webhook as service
from agent.schedules import store as schedules
from agent.webhooks import common
from agent.workspaces.routing import WorkspaceLookupError, repo_is_routable

router = APIRouter()


async def _launch_issue_automations(payload: dict[str, object], delivery_id: str) -> None:
    try:
        await schedules.launch_github_issue_automations(payload, delivery_id)
    except Exception:
        common.logger.exception(
            "Failed to launch GitHub issue automations",
            extra={"github_delivery": delivery_id},
        )


@router.post("/webhooks/github")
async def github_webhook(
    request: common.Request, response: Response, background_tasks: common.BackgroundTasks
) -> dict[str, str]:
    """Handle GitHub webhooks for issue and PR events that tag @open-swe."""
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not common.verify_github_signature(body, signature, secret=common.GITHUB_WEBHOOK_SECRET):
        common.logger.warning("Invalid GitHub webhook signature")
        raise common.HTTPException(status_code=401, detail="Invalid signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    common.logger.info(
        "GitHub webhook received",
        extra={
            "github_event": event_type,
            "github_delivery": delivery_id,
            "github_payload": body.decode("utf-8", errors="replace"),
        },
    )

    if event_type not in common.SUPPORTED_GH_EVENTS:
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

    if webhook_repo_config["owner"] and webhook_repo_config["name"]:
        repository = f"{webhook_repo_config['owner']}/{webhook_repo_config['name']}"
        try:
            routable = await repo_is_routable(
                webhook_repo_config["owner"], webhook_repo_config["name"]
            )
        except WorkspaceLookupError:
            # Ownership is unknown, so dropping the delivery may drop real work.
            # GitHub retries a 5xx and nothing else, so answer 503 and let it.
            common.logger.error(
                "Workspace lookup failed for a GitHub delivery; asking GitHub to retry",
                extra={"repository": repository, "github_delivery": delivery_id},
                exc_info=True,
            )
            response.status_code = 503
            return {"status": "error", "reason": "workspace ownership is temporarily unreadable"}
        if not routable:
            common.logger.info(
                "Ignoring GitHub event for a repository no workspace owns",
                extra={"repository": repository},
            )
            return {"status": "ignored", "reason": "repository is not assigned to a workspace"}

    # Ahead of the per-event branches below, several of which return early, but
    # behind both gates they answer to: the repository must belong to a
    # workspace and be allowlisted.
    if event_type in EXPEDITED_REVIEW_EVENTS and common.is_repo_allowed(webhook_repo_config):
        background_tasks.add_task(service.process_expedited_review_event, payload, event_type)

    issue = payload.get("issue", {})
    is_pull_request_comment = bool(event_type == "issue_comment" and issue.get("pull_request"))
    is_issue_comment = bool(event_type == "issue_comment" and not issue.get("pull_request"))
    is_issue_event = event_type == "issues"
    is_pull_request_event = event_type == "pull_request"

    if is_pull_request_event:
        action = payload.get("action", "")
        if action not in common.SUPPORTED_GH_PULL_REQUEST_ACTIONS:
            common.logger.info("Ignoring unsupported GitHub pull_request action: %s", action)
            return {
                "status": "ignored",
                "reason": f"Unsupported GitHub pull_request action: {action}",
            }
        if action in common.GH_PR_AGENT_STATE_ACTIONS:
            background_tasks.add_task(common.update_agent_thread_pr_state, payload)
        if action == "opened" or action in common.GH_PR_AGENT_STATE_ACTIONS:
            try:
                await common.update_agent_pr_usage_from_webhook(payload, delivery_id=delivery_id)
            except Exception:  # noqa: BLE001
                common.logger.debug("Failed to update Agent PR usage", exc_info=True)
        if action in common.GH_PR_WATCH_TOGGLE_ACTIONS:
            common.logger.info(
                "Accepted GitHub PR %s webhook, scheduling reviewer watch update", action
            )
            background_tasks.add_task(service.process_github_pr_close, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for reviewer watch"}
        if action in common.GH_PR_FIRST_REVIEW_ACTIONS:
            if not await common.is_repo_auto_review_enabled(webhook_repo_config):
                return {"status": "ignored", "reason": "Automatic review disabled for repository"}
            gate_rejection = await common.enforce_public_repo_org_gate(payload, "pull_request")
            if gate_rejection is not None:
                return gate_rejection
            common.logger.info("Accepted GitHub PR %s webhook, scheduling auto-review task", action)
            background_tasks.add_task(service.process_github_pr_ready, payload)
            return {"status": "accepted", "message": f"Processing PR {action} for auto-review"}
        if action in common.GH_PR_AGENT_STATE_ACTIONS:
            return {"status": "accepted", "message": f"Processing PR {action} state"}
        common.logger.info("Ignoring unsupported GitHub pull_request action: %s", action)
        return {
            "status": "ignored",
            "reason": f"Unsupported GitHub pull_request action: {action}",
        }

    if event_type == "push":
        if not await common.is_repo_auto_review_enabled(webhook_repo_config):
            return {"status": "ignored", "reason": "Automatic review disabled for repository"}
        common.logger.info("Accepted GitHub push webhook, scheduling reviewer watch evaluation")
        background_tasks.add_task(service.process_github_push_event, payload)
        return {"status": "accepted", "message": "Processing GitHub push for reviewer watch"}

    if not common.is_repo_allowed(webhook_repo_config):
        common.logger.debug(
            "Rejecting GitHub webhook: repo '%s/%s' not in allowlist",
            webhook_repo_config.get("owner"),
            webhook_repo_config.get("name"),
        )
        return {"status": "ignored", "reason": "Repository not in allowlist"}

    if event_type in common.GITHUB_CI_EVENTS:
        background_tasks.add_task(
            service.process_github_ci_event,
            payload,
            event_type,
            delivery_id,
        )
        return {"status": "accepted", "message": "Processing GitHub CI event"}

    if is_issue_event:
        action = payload.get("action", "")
        if action not in common.SUPPORTED_GH_ISSUE_ACTIONS:
            common.logger.info("Ignoring unsupported GitHub issue action: %s", action)
            return {"status": "ignored", "reason": f"Unsupported GitHub issue action: {action}"}
        if action == "edited":
            changes = payload.get("changes", {})
            if not any(field in changes for field in ("body", "title")):
                common.logger.info("Ignoring GitHub issue edit without title/body changes")
                return {"status": "ignored", "reason": "Issue edit did not change title or body"}
        if action == "opened":
            background_tasks.add_task(_launch_issue_automations, payload, delivery_id)

        issue_text = f"{issue.get('title', '')}\n\n{issue.get('body', '')}"
        if not common.mentions_open_swe(issue_text):
            tags = common.describe_open_swe_tags()
            common.logger.info("Ignoring issue that does not mention %s", tags)
            return {"status": "ignored", "reason": f"Issue does not mention {tags}"}

        gate_rejection = await common.enforce_public_repo_org_gate(payload, event_type)
        if gate_rejection is not None:
            return gate_rejection

        common.logger.info("Accepted GitHub issue webhook, scheduling background task")
        background_tasks.add_task(service.process_github_issue, payload, event_type)
        return {"status": "accepted", "message": "Processing GitHub issue event"}

    action = payload.get("action", "")
    supported_comment_actions = common.SUPPORTED_GH_COMMENT_ACTIONS.get(event_type)
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
        and common.review_comment_reply_parent_id(payload) is not None
    ):
        gate_rejection = await common.enforce_public_repo_org_gate(payload, event_type)
        if gate_rejection is not None:
            return gate_rejection
        background_tasks.add_task(service.process_github_review_finding_reply, payload)
        return {"status": "accepted", "message": "Processing review finding reply"}

    if not common.mentions_open_swe(comment_body):
        agent_thread_id = await service.untagged_agent_pr_thread_id(payload, event_type)
        if agent_thread_id is not None:
            gate_rejection = await common.enforce_public_repo_org_gate(payload, event_type)
            if gate_rejection is not None:
                return gate_rejection
            common.logger.info(
                "Accepted untagged GitHub comment on an agent-opened PR",
                extra={"github_event": event_type, "thread_id": agent_thread_id},
            )
            background_tasks.add_task(
                service.process_github_pr_comment,
                payload,
                event_type,
                agent_thread_id=agent_thread_id,
            )
            return {"status": "accepted", "message": f"Processing untagged {event_type} event"}
        tags = common.describe_open_swe_tags()
        common.logger.debug(
            "Ignoring GitHub %s%s that does not mention %s",
            event_type,
            f" action={action}" if action else "",
            tags,
        )
        return {"status": "ignored", "reason": f"Comment does not mention {tags}"}

    gate_rejection = await common.enforce_public_repo_org_gate(payload, event_type)
    if gate_rejection is not None:
        return gate_rejection

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
