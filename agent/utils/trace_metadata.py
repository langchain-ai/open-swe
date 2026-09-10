from collections.abc import Mapping
from typing import Any

from agent.run_config import RunConfig

_TRACE_PREFIX = "open_swe."


def _surface(source: str) -> str:
    if source == "dashboard" or source == "web":
        return "web"
    if source == "desktop":
        return "desktop"
    if source.startswith("slack"):
        return "slack"
    if source.startswith("github"):
        return "github"
    if source.startswith("linear"):
        return "linear"
    if source == "eval":
        return "eval"
    return "automation"


def _put(metadata: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and value != "":
        metadata[f"{_TRACE_PREFIX}{key}"] = value


def searchable_trace_metadata(
    configurable: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None = None,
    *,
    source: str | None = None,
    graph: str | None = None,
) -> dict[str, Any]:
    result = dict(metadata or {})
    cfg = RunConfig.parse(configurable)
    run_source = source or cfg.source or "automation"
    surface = _surface(run_source)
    client = cfg.client or surface
    origin = cfg.origin or cfg.source or run_source
    trigger_kind = cfg.trigger_kind
    if not trigger_kind:
        if cfg.background_task_completion:
            trigger_kind = "background_task"
        elif cfg.get("schedule_test") is True:
            trigger_kind = "schedule_test"
        elif cfg.schedule_id:
            trigger_kind = "schedule"
        elif surface in {"automation", "eval"}:
            trigger_kind = "automation"
        else:
            trigger_kind = "user"

    _put(result, "graph", graph)
    _put(result, "thread_id", cfg.thread_id)
    _put(result, "run_id", cfg.run_id)
    _put(result, "invocation_id", cfg.invocation_id)
    _put(result, "source", run_source)
    _put(result, "trigger_surface", client)
    _put(result, "client", client)
    _put(result, "execution", cfg.execution or ("local" if surface == "desktop" else "cloud"))
    _put(result, "thread_origin", origin)
    _put(result, "thread_origin_surface", _surface(origin))
    _put(
        result,
        "thread_category",
        cfg.thread_category
        or ("automation" if surface in {"automation", "eval"} else "interactive"),
    )
    _put(result, "trigger_kind", trigger_kind)

    slack = cfg.slack_thread
    if surface == "slack" and slack and slack.triggering_user_id:
        _put(result, "actor_id", f"slack:{slack.triggering_user_id}")
        _put(result, "actor_platform", "slack")
        _put(result, "actor_slack_user_id", slack.triggering_user_id)
    elif cfg.github_login:
        _put(result, "actor_id", f"github:{cfg.github_login}")
        _put(result, "actor_platform", "github")
    _put(result, "actor_github_login", cfg.github_login)
    _put(result, "actor_github_user_id", cfg.github_user_id)
    _put(result, "actor_email", slack.triggering_user_email if slack else cfg.user_email)
    if slack:
        _put(result, "actor_display_name", slack.triggering_user_name)
        _put(result, "actor_timezone", slack.triggering_user_timezone)
    _put(result, "actor_account_linked", bool(cfg.github_login))

    if cfg.repo:
        _put(result, "repo", cfg.repo.full_name)
        _put(result, "repo_owner", cfg.repo.owner)
        _put(result, "repo_name", cfg.repo.name)
    _put(result, "repo_private", cfg.repo_private)
    _put(result, "branch", cfg.branch_name)
    _put(result, "environment", cfg.environment)

    if slack:
        _put(result, "slack_channel_id", slack.channel_id)
        _put(result, "slack_thread_ts", slack.thread_ts)
        _put(result, "slack_triggering_event_ts", slack.triggering_event_ts)
    if cfg.linear_issue:
        _put(result, "linear_issue_id", cfg.linear_issue.id)
        _put(result, "linear_issue_identifier", cfg.linear_issue.identifier)
    if cfg.github_issue:
        _put(result, "github_issue_number", cfg.github_issue.number)
    _put(result, "pr_number", cfg.pr_number)
    _put(result, "pr_url", cfg.pr_url)
    _put(result, "head_sha", cfg.head_sha)
    _put(result, "base_sha", cfg.base_sha)

    _put(result, "agent_model", cfg.agent_model_id)
    _put(result, "agent_effort", cfg.agent_effort)
    _put(result, "reviewer_model", cfg.reviewer_model_id)
    _put(result, "reviewer_effort", cfg.reviewer_reasoning_effort)
    _put(result, "reviewer_subagent_model", cfg.reviewer_subagent_model_id)
    _put(result, "reviewer_subagent_effort", cfg.reviewer_subagent_reasoning_effort)
    _put(result, "chat_model", cfg.chat_model_id)
    _put(result, "chat_effort", cfg.chat_effort)

    _put(result, "plan_mode", cfg.plan_mode)
    _put(result, "admin_thread", cfg.admin_thread)
    _put(result, "schedule_id", cfg.schedule_id)
    _put(result, "watch_key", cfg.watch_key)
    _put(result, "background_task_completion", cfg.background_task_completion)
    _put(result, "eval", cfg.eval)
    _put(result, "reviewer_eval", cfg.reviewer_eval)
    return result
