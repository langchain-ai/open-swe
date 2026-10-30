"""What MDA's GitHub channel calls on the backend (``runtime_api.github``).

Open SWE's GitHub App delivers its webhooks to the MDA deployment's GitHub
channel, which verifies each delivery and decides there what it starts. It asks
here only for what needs Open SWE's data, its App or its signing key: to log each
delivery and say what Open SWE knows about its repository
(``record_github_delivery``); then, in one call (``act_on_github_delivery``), to run
the work it chose, as the backend's own webhook ran it, and to plan the runs the
channel starts: each automation's the delivery fires, on a thread of its own
(``openswe.schedules.store``), and the run an ``@openswe`` mention on an issue
starts on the channel's thread. Each run carries the backend's run-complete webhook.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Final, cast

from pydantic import JsonValue
from runtime_api.github import (
    ActOnGitHubDelivery,
    GitHubDelivery,
    GitHubDeliveryActed,
    GitHubDeliveryFacts,
    GitHubIssueRun,
    GitHubIssueRunRequest,
    GitHubWorkName,
    PlannedGitHubIssueRun,
)

from openswe import event_claims
from openswe.database import postgres
from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.github import webhook as service
from openswe.github.comments import OPEN_SWE_TAGS
from openswe.github.repo_cache import forget_cached_repos
from openswe.human_review.completed_reviews import CompletedReview
from openswe.mda_channels.api import ChannelCall
from openswe.merge_queue.controller import wake_native_queue
from openswe.review_guide.launch import notify_pr_updated
from openswe.schedules import store as schedules
from openswe.threads.router import (
    adopt_channel_run,
    channel_run_metadata,
    channel_run_starting,
)
from openswe.threads.runtimes import RUNTIMES
from openswe.ui_invalidations import Topic
from openswe.webhooks import common
from openswe.webhooks.event_log import EventLog, EventRefs
from openswe.workspaces.routing import repo_is_routable

logger = logging.getLogger(__name__)

CHANNEL: Final = "github"
_WORK_CLAIMS: Final = "github_channel_work"
# Longer than MDA keeps sending one call again.
_WORK_CLAIM_TTL: Final = timedelta(hours=24)

type _Payload = dict[str, Any]


def _repo_config(payload: dict[str, JsonValue]) -> dict[str, str]:
    repository = payload.get("repository")
    repository = repository if isinstance(repository, dict) else {}
    owner = repository.get("owner")
    owner = owner if isinstance(owner, dict) else {}
    return {"owner": str(owner.get("login") or ""), "name": str(repository.get("name") or "")}


async def record_github_delivery(
    call: ChannelCall, delivery: GitHubDelivery
) -> GitHubDeliveryFacts:
    """Log the delivery once, and what Open SWE knows about its repository.

    A workspace lookup that fails raises, so the delivery fails rather than drop
    work a workspace may own.
    """
    body = json.dumps(delivery.payload).encode()
    refs = EventRefs.github(body)
    await EventLog.record_once(
        call.request,
        body,
        "github",
        event_type=delivery.event,
        delivery_id=delivery.delivery_id,
        refs=refs,
    )
    if refs.pull_request is not None:
        await Topic.PULL_REQUESTS.invalidate(key=refs.pull_request)
    repo = _repo_config(delivery.payload)
    named = bool(repo["owner"] and repo["name"])
    reviews = named and delivery.event in {"pull_request", "push"}
    return GitHubDeliveryFacts(
        routable=not named or await repo_is_routable(repo["owner"], repo["name"]),
        allowed=common.is_repo_allowed(repo),
        auto_review=reviews and await common.is_repo_auto_review_enabled(repo),
        tags=list(OPEN_SWE_TAGS),
    )


async def _gated(payload: _Payload, event: str) -> bool:
    """Whether the public-repo gate lets the delivery's sender in; it logs why when not."""
    return await common.enforce_public_repo_org_gate(payload, event) is None


async def _review_pull_request(payload: _Payload) -> None:
    if await _gated(payload, "pull_request"):
        await service.process_github_pr_ready(payload)


async def _route_comment(payload: _Payload, event: str, *, mentioned: bool) -> None:
    """A pull request comment's run, as the backend's webhook routed it."""
    sender_login = str((payload.get("sender") or {}).get("login") or "")
    if not await service.is_accepted_commenter(sender_login):
        logger.debug(
            "Ignoring GitHub comment from an unregistered sender",
            extra={"github_event": event, "sender_login": sender_login},
        )
        return
    if (
        event == "pull_request_review_comment"
        and common.review_comment_reply_parent_id(payload) is not None
    ):
        if await _gated(payload, event):
            await service.process_github_review_finding_reply(payload)
        return
    agent_thread_id = None
    if not mentioned:
        agent_thread_id = await service.untagged_agent_pr_thread_id(payload, event)
        if agent_thread_id is None:
            return
    if await _gated(payload, event):
        await service.process_github_pr_comment(payload, event, agent_thread_id=agent_thread_id)


def _tasks(work: ActOnGitHubDelivery) -> dict[GitHubWorkName, Callable[[], Awaitable[object]]]:
    payload = cast(_Payload, work.payload)
    event, delivery_id = work.event, work.delivery_id
    return {
        "wake_merge_queue": lambda: wake_native_queue(payload),
        "forget_cached_repos": forget_cached_repos,
        "track_pull_request_state": lambda: common.update_agent_thread_pr_state(payload),
        "track_pull_request_usage": lambda: common.update_agent_pr_usage_from_webhook(
            payload, delivery_id=delivery_id
        ),
        "withdraw_superseded_approvals": lambda: service.withdraw_superseded_approvals(payload),
        "notify_pr_updated": lambda: notify_pr_updated(payload),
        "review_fork_push": lambda: service.process_github_pr_synchronize(payload),
        "settle_review_on_close": lambda: service.settle_human_review_on_close(payload),
        "settle_human_reviews": lambda: service.settle_human_reviews(payload),
        "update_reviewer_watch": lambda: service.process_github_pr_close(payload),
        "review_pull_request": lambda: _review_pull_request(payload),
        "review_push": lambda: service.process_github_push_event(payload),
        "ci_event": lambda: service.process_github_ci_event(payload, event, delivery_id),
        "record_completed_review": lambda: CompletedReview.record(payload),
        "route_comment": lambda: _route_comment(payload, event, mentioned=work.mentioned),
    }


async def _run(
    name: GitHubWorkName, task: Callable[[], Awaitable[object]], work: ActOnGitHubDelivery
) -> None:
    # One piece failing doesn't keep the delivery's others from running.
    try:
        await task()
    except Exception:
        logger.exception(
            "GitHub work failed",
            extra={
                "github_work": name,
                "github_event": work.event,
                "github_delivery": work.delivery_id,
            },
        )


async def _accept_work(call: ChannelCall, work: ActOnGitHubDelivery) -> None:
    """Run the work after answering, in the order given, once per delivery: GitHub sends
    a delivery again when someone redelivers it."""
    if postgres.configured() and not await event_claims.claim(
        _WORK_CLAIMS, work.delivery_id, ttl=_WORK_CLAIM_TTL
    ):
        return
    tasks = _tasks(work)
    for name in dict.fromkeys(work.work):
        call.background.add_task(_run, name, tasks[name], work)
    logger.info(
        "Accepted GitHub work",
        extra={
            "github_event": work.event,
            "github_delivery": work.delivery_id,
            "github_work": list(work.work),
        },
    )


async def _prepare_run(plan: ActOnGitHubDelivery, thread_id: str) -> GitHubIssueRun | None:
    prepared = await service.prepare_github_issue_run(
        cast(_Payload, plan.payload),
        plan.event,
        thread_id,
        adopt=lambda thread: adopt_channel_run(thread, CHANNEL),
    )
    if prepared is None:
        return None
    run_config = await prepare_durable_run(
        "agent",
        {"configurable": prepared.configurable},
        {
            **common.AGENT_VERSION_METADATA,
            CHANNEL_METADATA_KEY: CHANNEL,
            "github_delivery": plan.delivery_id,
        },
        source="github_issue",
    )
    rewritten = await RUNTIMES.mda_runtime.run_payload(
        thread_id,
        {
            "assistant_id": "agent",
            "config": cast(JsonValue, run_config),
            # So the attribution the run's hooks read rides in its run token.
            "metadata": cast(JsonValue, run_config["metadata"]),
        },
        # A channel thread has no Slack thread to link.
        thread_metadata={},
    )
    run = GitHubIssueRun(
        input=cast(dict[str, JsonValue], prepared.input),
        context=cast(dict[str, JsonValue], rewritten["context"]),
        metadata=channel_run_metadata(run_config["metadata"]),
        webhook=COMPLETION_WEBHOOK_URL or None,
    )
    await channel_run_starting(thread_id)
    return run


async def _plan_issue_run(
    plan: ActOnGitHubDelivery, request: GitHubIssueRunRequest
) -> PlannedGitHubIssueRun:
    """The run an ``@openswe`` mention on an issue starts on the channel's thread.

    The channel decided the mention is one; this checks the commenter and the
    public-repo gate, reacts to the comment, builds the prompt, indexes the thread
    and signs the run. While the thread waits it only says whether Open SWE acts.
    """
    payload = cast(_Payload, plan.payload)
    thread_id = str(request.thread_id)
    fields = {"github_event": plan.event, "github_delivery": plan.delivery_id}
    if plan.event == "issue_comment":
        sender_login = str((payload.get("sender") or {}).get("login") or "")
        if not await service.is_accepted_commenter(sender_login):
            return _no_run("Sender is not a registered Open SWE user", **fields)
    if not await _gated(payload, plan.event):
        return _no_run("The public-repo gate refused the sender", **fields)
    if request.waiting:
        # Nothing starts while the thread waits; no reaction or metadata either.
        return PlannedGitHubIssueRun(starts=True)
    run = await _prepare_run(plan, thread_id)
    if run is None:
        return _no_run("no run prepared", **fields, agent_thread_id=thread_id)
    return PlannedGitHubIssueRun(starts=True, run=run)


async def act_on_github_delivery(
    call: ChannelCall, act: ActOnGitHubDelivery
) -> GitHubDeliveryActed:
    """Take the work the channel chose for a delivery, and plan the runs it starts."""
    if act.work:
        await _accept_work(call, act)
    automations = (
        await schedules.plan_github_automations(
            act.event, cast(_Payload, act.payload), act.delivery_id
        )
        if act.automations
        else []
    )
    issue_run = None if act.issue_run is None else await _plan_issue_run(act, act.issue_run)
    return GitHubDeliveryActed(issue_run=issue_run, automations=automations)


def _no_run(reason: str, **fields: str) -> PlannedGitHubIssueRun:
    """Answer the channel that the mention starts nothing, with one log line saying why."""
    logger.info(
        "MDA's GitHub channel starts no run",
        extra={"endpoint": "github", "no_run_reason": reason, **fields},
    )
    return PlannedGitHubIssueRun(starts=False)
