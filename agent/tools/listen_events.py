"""Tools for listening to inbound events: subscribe a thread, and list what arrives."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn
from uuid import UUID

from pydantic import JsonValue

from agent.github.pull_requests import PullRequest
from agent.github.repositories import Repository
from agent.review.styles import normalize_repo_full_name
from agent.run_config import RunConfig
from agent.slack.client import parse_github_pr_url
from agent.tools.errors import ToolError
from agent.webhooks.event_log import RETAINED_DAYS, EventLog, WebhookSource
from agent.webhooks.event_matches import MultitaskStrategy
from agent.webhooks.event_subscriptions import EventSubscription
from agent.workspaces.routing import workspace_for_repo
from agent.workspaces.store import DEFAULT_WORKSPACE_SLUG, WORKSPACES

logger = logging.getLogger(__name__)

type WhenBusy = Literal["wait", "interrupt"]

_STRATEGIES: dict[WhenBusy, MultitaskStrategy] = {"wait": "enqueue", "interrupt": "interrupt"}
_WHEN_BUSY: dict[MultitaskStrategy, WhenBusy] = {v: k for k, v in _STRATEGIES.items()}
_DEFAULT_HOURS = 24 * 7
_MAX_HOURS = 24 * 14
_MAX_FILTERS = 20
_MAX_PER_THREAD = 5
_MAX_INSTRUCTIONS_CHARS = 4_000
_MAX_PAYLOAD_MATCH_CHARS = 4_000
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
        "target": subscription.target,
        "sources": subscription.sources,
        "event_types": subscription.event_types,
        "payload_match": subscription.payload_match,
        "when_busy": _WHEN_BUSY[subscription.multitask_strategy],
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


def _error(message: str) -> NoReturn:
    raise ToolError(message)


async def listen_events(
    action: Literal["subscribe", "list", "cancel"],
    sources: list[WebhookSource] | None = None,
    repo: str = "",
    pr_url: str = "",
    event_types: list[str] | None = None,
    payload_match: dict[str, JsonValue] | None = None,
    when_busy: WhenBusy = "wait",
    one_shot: bool = False,
    instructions: str = "",
    expires_in_hours: int = _DEFAULT_HOURS,
    subscription_id: str = "",
) -> dict[str, object]:
    """Implement the `listen_events` tool."""
    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return _error("No thread_id in current run config")
    if action == "list":
        subscriptions = await EventSubscription.for_thread(thread_id)
        return {"success": True, "subscriptions": [_describe(item) for item in subscriptions]}
    if action == "cancel":
        try:
            cancelled_id = UUID(subscription_id)
        except ValueError:
            return _error("subscription_id must be a subscription's id")
        return {
            "success": True,
            "cancelled": await EventSubscription.cancel(thread_id, cancelled_id),
        }

    types = _filters(event_types)
    if not (types or repo or pr_url):
        return _error("Narrow the subscription with event_types, repo, or pr_url")
    if len(types) > _MAX_FILTERS:
        return _error(f"event_types accepts at most {_MAX_FILTERS} values")
    if len(json.dumps(payload_match or {})) > _MAX_PAYLOAD_MATCH_CHARS:
        return _error(
            f"payload_match must be at most {_MAX_PAYLOAD_MATCH_CHARS} characters of JSON"
        )
    if not 1 <= expires_in_hours <= _MAX_HOURS:
        return _error(f"expires_in_hours must be between 1 and {_MAX_HOURS}")
    if len(instructions) > _MAX_INSTRUCTIONS_CHARS:
        return _error(f"instructions must be at most {_MAX_INSTRUCTIONS_CHARS} characters")
    if len(await EventSubscription.for_thread(thread_id)) >= _MAX_PER_THREAD:
        return _error(
            f"A thread can hold at most {_MAX_PER_THREAD} subscriptions; cancel one first"
        )

    workspace_slug = cfg.workspace_slug or DEFAULT_WORKSPACE_SLUG
    workspace_id = await WORKSPACES.id_for_slug(workspace_slug)
    if workspace_id is None:
        return _error(f"Workspace {workspace_slug} does not exist")

    pr_ref = parse_github_pr_url(pr_url) if pr_url else None
    if pr_url and pr_ref is None:
        return _error("pr_url must be a GitHub pull request URL")
    if pr_ref is not None:
        repo = f"{pr_ref.owner}/{pr_ref.repo}"
    repository: Repository | None = None
    if repo:
        try:
            full_name = normalize_repo_full_name(repo)
        except ValueError:
            return _error("repo must be owner/name")
        owner, name = full_name.split("/")
        repository = await Repository.get(full_name)
        if repository is None or await workspace_for_repo(owner, name) != workspace_slug:
            return _error(f"{full_name} is not a repository of workspace {workspace_slug}")
    pull_request = (
        await (await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)).ensure()
        if pr_ref is not None
        else None
    )

    dumped = cfg.dump()
    subscription = await EventSubscription(
        thread_id=thread_id,
        workspace_id=workspace_id,
        sources=sorted(set(sources or ())),
        repository_id=repository.id if repository else None,
        pull_request_id=pull_request.id if pull_request else None,
        event_types=types,
        payload_match=payload_match or {},
        multitask_strategy=_STRATEGIES[when_busy],
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


async def list_event_types(
    source: WebhookSource | None = None, event_type: str = ""
) -> dict[str, object]:
    """Implement the `list_event_types` tool."""
    since = datetime.now(UTC) - timedelta(days=RETAINED_DAYS)
    kinds = await EventLog.kinds(since, source=source, event_type=event_type.strip())
    return {
        "since": since.isoformat(),
        "event_types": [kind.model_dump(mode="json") for kind in kinds],
    }
