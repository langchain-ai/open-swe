"""The Linear comment trigger: an @-mention on an issue starts a run on its thread."""

import logging

from agent.dashboard.agent_overrides import resolve_login_from_email_async
from agent.linear.client import post_linear_trace_comment, react_to_linear_comment
from agent.linear.schema import LinearComment, LinearIssue
from agent.linear.sessions import (
    build_run_input,
    dispatch_linear_run,
    first_identified,
    linear_issue_config,
    replay_comments,
    run_configurable,
    system_text,
    upsert_thread_metadata,
)
from agent.thread_ids import linear_issue_thread_id

logger = logging.getLogger(__name__)


async def process_linear_issue(
    issue: LinearIssue,
    repo_config: dict[str, str],
    *,
    trigger: LinearComment | None = None,
) -> None:
    """Start a run for a Linear issue, replaying the conversation the mention sits in."""
    if trigger is not None and trigger.id:
        await react_to_linear_comment(trigger.id, "👀")

    requester = first_identified(
        trigger.user if trigger is not None else None, issue.creator, issue.assignee
    )
    requester_email = (requester.email or "") if requester is not None else ""
    requester_name = (requester.name or requester.display_name or "") if requester else ""
    github_login = (
        await resolve_login_from_email_async(requester_email) if requester_email else None
    )

    run_input, model_override = await build_run_input(
        issue=issue,
        repo_config=repo_config,
        text=system_text(None, issue, repo_config, user_name=requester_name),
        comments=replay_comments(issue, trigger=trigger),
        github_login=github_login,
    )

    thread_id = linear_issue_thread_id(issue.id)
    linear_issue = linear_issue_config(issue, user_name=requester_name)
    await upsert_thread_metadata(
        thread_id,
        issue=issue,
        repo_config=repo_config,
        linear_issue=linear_issue,
        github_login=github_login,
        user_email=requester_email,
    )
    await dispatch_linear_run(
        thread_id,
        run_configurable(
            repo_config=repo_config,
            linear_issue=linear_issue,
            user_email=requester_email,
            github_login=github_login,
            model_override=model_override,
        ),
        run_input,
    )
    await post_linear_trace_comment(issue.id, thread_id, trigger.id if trigger is not None else "")
