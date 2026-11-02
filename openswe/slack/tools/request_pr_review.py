from langgraph.config import get_config
from langgraph_sdk import get_client

from openswe.agent_api.tokens import AGENT_TURN_KEY
from openswe.audit_logs.tools import audit_tool
from openswe.review.goal_review import GoalReview
from openswe.slack.client import GitHubPrRef, get_active_slack_thread, parse_github_pr_url
from openswe.threads.goal import ThreadGoal
from openswe.tools.mcp_exposure import expose_mcp
from openswe.utils.dashboard_links import dashboard_review_url


async def trigger_pr_review_from_ref(
    pr_ref: GitHubPrRef,
    *,
    source: str,
    github_login: str = "",
    github_user_id: int | None = None,
    slack_channel_id: str = "",
    slack_thread_ts: str = "",
    allow_draft: bool = False,
    goal_review: GoalReview | None = None,
) -> dict[str, object]:
    from openswe.github.webhook import trigger_pr_review_from_ref as _trigger_pr_review_from_ref

    return await _trigger_pr_review_from_ref(
        pr_ref,
        source=source,
        github_login=github_login,
        github_user_id=github_user_id,
        slack_channel_id=slack_channel_id,
        slack_thread_ts=slack_thread_ts,
        allow_draft=allow_draft,
        goal_review=goal_review,
    )


@expose_mcp()
@audit_tool()
async def request_pr_review(pr_url: str) -> dict[str, object]:
    """Implement the `request_pr_review` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if not pr_ref:
        return {
            "success": False,
            "error": "Expected a GitHub PR URL like https://github.com/OWNER/REPO/pull/NUMBER",
        }

    configurable = get_config().get("configurable", {})
    source = configurable.get("source") or "agent"
    github_login = configurable.get("github_login", "")
    thread_id = configurable.get("thread_id")
    # A Goal mode run's review comes back to it to address; the person hears nothing.
    turn = configurable.get(AGENT_TURN_KEY)
    goal_mode = isinstance(turn, dict) and turn.get("goal_mode") is True
    goal_review = (
        GoalReview(thread_id=thread_id) if goal_mode and isinstance(thread_id, str) else None
    )
    slack_thread = configurable.get("slack_thread") or {}
    active = await get_active_slack_thread(
        get_client(),
        thread_id if isinstance(thread_id, str) else None,
        slack_thread if isinstance(slack_thread, dict) else None,
    )
    slack_thread = {} if goal_mode else active or {}
    result = await trigger_pr_review_from_ref(
        pr_ref,
        source=source,
        github_login=github_login,
        github_user_id=configurable.get("github_user_id"),
        slack_channel_id=slack_thread.get("channel_id", ""),
        slack_thread_ts=slack_thread.get("thread_ts", ""),
        allow_draft=goal_mode,
        goal_review=goal_review,
    )
    if result.get("success") and (
        review_url := dashboard_review_url(pr_ref.owner, pr_ref.repo, pr_ref.number)
    ):
        result["review_url"] = review_url
    if result.get("success") and result.get("goal_review") and goal_review is not None:
        await ThreadGoal(goal_review.thread_id).review_requested(pr_ref.url)
    return result
