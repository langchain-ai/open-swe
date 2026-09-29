"""Tools for listening to inbound events: subscribe a thread, and list what arrives."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from agent.github.pull_requests import PullRequest
from agent.run_config import RunConfig
from agent.slack.client import parse_github_pr_url
from agent.webhooks.event_log import RETAINED_DAYS, EventLog
from agent.webhooks.event_subscriptions import EventSubscription, MultitaskStrategy
from agent.workspaces.routing import workspace_for_repo
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG

logger = logging.getLogger(__name__)

_DEFAULT_HOURS = 24 * 7
_MAX_HOURS = 24 * 14
_MAX_FILTERS = 20
_MAX_PER_THREAD = 5
_MAX_INSTRUCTIONS_CHARS = 4_000
_CARRIED_CONFIG_KEYS = frozenset(
    {
        "thread_id",
        "source",
        "workspace",
        "environment",
        "github_login",
        "github_user_id",
        "user_email",
        "repo",
        "slack_thread",
        "linear_issue",
        "github_issue",
        "agent_model_id",
        "agent_effort",
        "model_selection",
        "admin_thread",
        "draft_prs",
    }
)


def _describe(subscription: EventSubscription) -> dict[str, object]:
    return {
        "subscription_id": str(subscription.id),
        "pr_url": subscription.pull_request.url,
        "event_types": subscription.event_types,
        "actions": subscription.actions,
        "multitask_strategy": subscription.multitask_strategy,
        "one_shot": subscription.one_shot,
        "instructions": subscription.instructions,
        "trigger_count": subscription.trigger_count,
        "last_triggered_at": subscription.last_triggered_at.isoformat()
        if subscription.last_triggered_at
        else None,
        "expires_at": subscription.expires_at.isoformat(),
    }


def _filters(values: list[str] | None) -> list[str]:
    return sorted({value.strip() for value in values or () if value.strip()})


async def listen_events(
    action: Literal["subscribe", "list", "cancel"],
    pr_url: str = "",
    subscription_id: str = "",
    event_types: list[str] | None = None,
    actions: list[str] | None = None,
    multitask_strategy: MultitaskStrategy = "enqueue",
    one_shot: bool = False,
    instructions: str = "",
    expires_in_hours: int = _DEFAULT_HOURS,
) -> dict[str, object]:
    """Implement the `listen_events` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "No thread_id in current run config"}
    if action == "list":
        subscriptions = await EventSubscription.for_thread(thread_id)
        return {"success": True, "subscriptions": [_describe(item) for item in subscriptions]}
    if action == "cancel":
        try:
            cancelled_id = UUID(subscription_id)
        except ValueError:
            return {"success": False, "error": "subscription_id must be a subscription's id"}
        return {
            "success": True,
            "cancelled": await EventSubscription.cancel(thread_id, cancelled_id),
        }

    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return {"success": False, "error": "pr_url must be a GitHub pull request URL"}
    if len(await EventSubscription.for_thread(thread_id)) >= _MAX_PER_THREAD:
        return {
            "success": False,
            "error": f"A thread can hold at most {_MAX_PER_THREAD} subscriptions; cancel one first",
        }
    thread_workspace = cfg.workspace_slug or DEFAULT_WORKSPACE_SLUG
    repo_workspace = await workspace_for_repo(pr_ref.owner, pr_ref.repo) or DEFAULT_WORKSPACE_SLUG
    if repo_workspace != thread_workspace:
        return {
            "success": False,
            "error": f"{pr_ref.owner}/{pr_ref.repo} does not belong to this thread's workspace",
        }
    if not 1 <= expires_in_hours <= _MAX_HOURS:
        return {"success": False, "error": f"expires_in_hours must be between 1 and {_MAX_HOURS}"}
    if len(instructions) > _MAX_INSTRUCTIONS_CHARS:
        return {
            "success": False,
            "error": f"instructions must be at most {_MAX_INSTRUCTIONS_CHARS} characters",
        }
    types, wanted_actions = _filters(event_types), _filters(actions)
    if max(len(types), len(wanted_actions)) > _MAX_FILTERS:
        return {
            "success": False,
            "error": f"event_types and actions accept at most {_MAX_FILTERS} values each",
        }

    pull_request = await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)
    pull_request = await pull_request.ensure()
    dumped = cfg.dump()
    subscription = await EventSubscription(
        thread_id=thread_id,
        pull_request_id=pull_request.id,
        event_types=types,
        actions=wanted_actions,
        multitask_strategy=multitask_strategy,
        one_shot=one_shot,
        instructions=instructions.strip(),
        run_config={key: value for key, value in dumped.items() if key in _CARRIED_CONFIG_KEYS},
        expires_at=datetime.now(UTC) + timedelta(hours=expires_in_hours),
    ).create()
    logger.info(
        "Event subscription saved",
        extra={"event_subscription_id": str(subscription.id), "agent_thread_id": thread_id},
    )
    return {"success": True, "subscription": _describe(subscription)}


async def list_event_types() -> dict[str, object]:
    """Implement the `list_event_types` tool."""
    since = datetime.now(UTC) - timedelta(days=RETAINED_DAYS)
    kinds = await EventLog.kinds(since)
    return {
        "since": since.isoformat(),
        "event_types": [kind.model_dump(mode="json") for kind in kinds],
    }
