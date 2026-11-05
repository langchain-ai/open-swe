"""What MDA's GitHub channel calls on the backend (``runtime_api.github``).

Open SWE's GitHub App delivers its webhooks to the MDA deployment's GitHub
channel, which verifies each delivery and hands every one to the backend
(``record_github_delivery``). The backend logs it, runs its own work for it
(reviews, walkthroughs, human review, the merge queue, CI and the pull request
state the dashboard shows, ``openswe.github.work``), and answers what Open SWE
knows about its repository. The channel decides only what starts an agent run,
asking here for what needs Open SWE's data, its App or its signing key: the
repository's automations, which the channel
matches and writes each run's prompt for (``github_automation_rules``); for an
``@openswe`` mention on an issue, who among the people its prompt quotes is an
Open SWE user, and what the issue's thread already holds
(``github_issue_facts``), from which the channel writes the run's input; then, in
one call (``act_on_github_delivery``), to plan the runs the channel starts: each automation's
the channel matched, on a thread of its own (``openswe.schedules.store``), a pull
request comment's when it goes to the pull request's own thread, and the issue
mention's on the channel's thread. Each run carries the backend's run-complete
webhook. A comment's run on the agent's thread, or on a thread the backend owns,
the backend still writes and starts itself: it reads the pull request's comments
with the person's token and keeps a private thread's to its owner's.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any, Final, cast
from uuid import UUID

from pydantic import JsonValue
from runtime_api.github import (
    ActOnGitHubDelivery,
    GitHubAutomation,
    GitHubAutomationRules,
    GitHubAutomationRulesRequest,
    GitHubDelivery,
    GitHubDeliveryActed,
    GitHubDeliveryFacts,
    GitHubIssueFacts,
    GitHubIssueFactsRequest,
    GitHubIssueRun,
    GitHubIssueRunRequest,
    GitHubThreadRun,
    GitHubWorkName,
    PlannedGitHubIssueRun,
)

from openswe import event_claims
from openswe.database import postgres
from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.github import webhook as service
from openswe.github.comments import OPEN_SWE_TAGS
from openswe.github.repo_cache import forget_cached_repos
from openswe.github.work import delivery_work
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
from openswe.users import User
from openswe.webhooks import common
from openswe.webhooks.event_log import EventLog, EventRefs
from openswe.workspaces.routing import repo_is_routable

logger = logging.getLogger(__name__)

CHANNEL: Final = service.GITHUB_CHANNEL
_WORK_CLAIMS: Final = "github_channel_work"
# A comment's run the backend starts itself, once per delivery.
_COMMENT_CLAIMS: Final = "github_channel_comment"
# Longer than MDA keeps sending one call again.
_WORK_CLAIM_TTL: Final = timedelta(hours=24)

type _Payload = dict[str, Any]
# Pull request actions the public-repo gate checks the sender of before the delivery
# starts anything (``GitHubDeliveryFacts.gated``).
_GATED_PULL_REQUEST_ACTIONS: Final = frozenset(
    {"opened", "reopened", "ready_for_review", "synchronize"}
)


def _repo_config(payload: dict[str, JsonValue]) -> dict[str, str]:
    repository = payload.get("repository")
    repository = repository if isinstance(repository, dict) else {}
    owner = repository.get("owner")
    owner = owner if isinstance(owner, dict) else {}
    return {"owner": str(owner.get("login") or ""), "name": str(repository.get("name") or "")}


async def record_github_delivery(
    call: ChannelCall, delivery: GitHubDelivery
) -> GitHubDeliveryFacts:
    """Log the delivery once, run the backend's own work for it after answering, and
    say what Open SWE knows about its repository, for the channel's agent runs.

    A repository outside the allowlist is neither logged nor looked up: its delivery
    starts nothing. A workspace lookup that fails raises, so the delivery fails rather
    than drop work a workspace may own.
    """
    body = json.dumps(delivery.payload).encode()
    refs = EventRefs.github(body)
    # The owner alone, as installation events name it, passes when any of its repositories do.
    named_repo = {"owner": refs.github_owner, "name": refs.github_repository.partition("/")[2]}
    if refs.github_owner and not common.is_repo_allowed(named_repo):
        logger.info(
            "Dropping GitHub delivery for a repository outside the allowlist",
            extra={"github_owner": refs.github_owner, "github_delivery": delivery.delivery_id},
        )
        return GitHubDeliveryFacts(
            routable=False, allowed=False, auto_review=False, tags=list(OPEN_SWE_TAGS)
        )
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
    payload = cast(_Payload, delivery.payload)
    gates = (
        delivery.event == "pull_request" and payload.get("action") in _GATED_PULL_REQUEST_ACTIONS
    )
    facts = GitHubDeliveryFacts(
        routable=not named or await repo_is_routable(repo["owner"], repo["name"]),
        allowed=True,
        gated=gates and not await _gated(payload, "pull_request"),
        auto_review=reviews and await common.is_repo_auto_review_enabled(repo),
        tags=list(OPEN_SWE_TAGS),
    )
    if work := delivery_work(delivery.event, delivery.payload, facts):
        await _accept_work(call, delivery, work)
    return facts


async def _gated(payload: _Payload, event: str) -> bool:
    """Whether the public-repo gate lets the delivery's sender in; it logs why when not."""
    return await common.enforce_public_repo_org_gate(payload, event) is None


async def _review_pull_request(payload: _Payload) -> None:
    if await _gated(payload, "pull_request"):
        await service.process_github_pr_ready(payload)


class _CommentChannel:
    """MDA's GitHub channel, for a pull request comment's run (``service.PrCommentChannel``)."""

    def __init__(self, act: ActOnGitHubDelivery) -> None:
        self._act = act
        # The run the channel starts, once a comment's run goes to the PR's own thread.
        self.run: GitHubThreadRun | None = None

    async def adopt(self, thread_id: str, metadata: dict[str, JsonValue]) -> None:
        await adopt_channel_run(thread_id, CHANNEL, metadata=metadata)

    async def start(self, thread_id: str, configurable: dict[str, object], run_input: Any) -> bool:
        self.run = GitHubThreadRun(
            thread_id=UUID(thread_id),
            input=cast(dict[str, JsonValue], run_input),
            **await _signed(self._act, thread_id, configurable, source="github"),
        )
        return True

    async def once(self) -> bool:
        # MDA may send the call again after an answer it didn't get.
        return not postgres.configured() or await event_claims.claim(
            _COMMENT_CLAIMS, self._act.delivery_id, ttl=_WORK_CLAIM_TTL
        )


async def _route_comment(act: ActOnGitHubDelivery) -> GitHubThreadRun | None:
    """A pull request comment's run, as the backend's webhook routed it.

    The channel starts it when it goes to the pull request's own thread; the
    backend starts any other itself, once per delivery.
    """
    payload, event = cast(_Payload, act.payload), act.event
    sender_login = str((payload.get("sender") or {}).get("login") or "")
    if not await service.is_accepted_commenter(sender_login):
        logger.debug(
            "Ignoring GitHub comment from an unregistered sender",
            extra={"github_event": event, "sender_login": sender_login},
        )
        return None
    channel = _CommentChannel(act)
    if (
        event == "pull_request_review_comment"
        and common.review_comment_reply_parent_id(payload) is not None
    ):
        if await _gated(payload, event) and await channel.once():
            await service.process_github_review_finding_reply(payload)
        return None
    agent_thread_id = None
    if not act.mentioned:
        agent_thread_id = await service.untagged_agent_pr_thread_id(payload, event)
        if agent_thread_id is None:
            return None
    if await _gated(payload, event):
        await service.process_github_pr_comment(
            payload, event, agent_thread_id=agent_thread_id, channel=channel
        )
    return channel.run


def _tasks(
    delivery: GitHubDelivery,
) -> dict[GitHubWorkName, Callable[[], Awaitable[object]]]:
    payload = cast(_Payload, delivery.payload)
    event, delivery_id = delivery.event, delivery.delivery_id
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
        # The gate let the sender in as the delivery was routed (``GitHubDeliveryFacts.gated``).
        "walkthrough_pull_request": lambda: service.process_github_pr_walkthrough(payload),
        "sync_review_thread": lambda: service.process_github_review_thread(payload),
        "review_push": lambda: service.process_github_push_event(payload),
        "ci_event": lambda: service.process_github_ci_event(payload, event, delivery_id),
        "record_completed_review": lambda: CompletedReview.record(payload),
    }


async def _run(
    name: GitHubWorkName, task: Callable[[], Awaitable[object]], delivery: GitHubDelivery
) -> None:
    # One piece failing doesn't keep the delivery's others from running.
    try:
        await task()
    except Exception:
        logger.exception(
            "GitHub work failed",
            extra={
                "github_work": name,
                "github_event": delivery.event,
                "github_delivery": delivery.delivery_id,
            },
        )


async def _accept_work(
    call: ChannelCall, delivery: GitHubDelivery, work: Sequence[GitHubWorkName]
) -> None:
    """Run the work after answering, in the order given, once per delivery: GitHub sends
    a delivery again when someone redelivers it."""
    if postgres.configured() and not await event_claims.claim(
        _WORK_CLAIMS, delivery.delivery_id, ttl=_WORK_CLAIM_TTL
    ):
        return
    tasks = _tasks(delivery)
    for name in dict.fromkeys(work):
        call.background.add_task(_run, name, tasks[name], delivery)
    logger.info(
        "Accepted GitHub work",
        extra={
            "github_event": delivery.event,
            "github_delivery": delivery.delivery_id,
            "github_work": list(work),
        },
    )


async def _prepare_run(
    plan: ActOnGitHubDelivery, request: GitHubIssueRunRequest
) -> GitHubIssueRun | None:
    thread_id = str(request.thread_id)
    configurable = await service.prepare_github_issue_run(
        cast(_Payload, plan.payload),
        thread_id,
        workspace=request.workspace,
        adopt=lambda thread: adopt_channel_run(thread, CHANNEL),
    )
    if configurable is None:
        return None
    return GitHubIssueRun(**await _signed(plan, thread_id, configurable, source="github_issue"))


async def _signed(
    act: ActOnGitHubDelivery, thread_id: str, configurable: Mapping[str, object], *, source: str
) -> dict[str, Any]:
    """The run's signed context, metadata and run-complete webhook, for the channel to
    start it on ``thread_id``; the thread is busy from here on."""
    run_config = await prepare_durable_run(
        "agent",
        {"configurable": dict(configurable)},
        {
            **common.AGENT_VERSION_METADATA,
            CHANNEL_METADATA_KEY: CHANNEL,
            "github_delivery": act.delivery_id,
        },
        source=source,
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
    await channel_run_starting(thread_id)
    return {
        "context": cast(dict[str, JsonValue], rewritten["context"]),
        "metadata": channel_run_metadata(run_config["metadata"]),
        "webhook": COMPLETION_WEBHOOK_URL or None,
    }


async def _plan_issue_run(
    plan: ActOnGitHubDelivery, request: GitHubIssueRunRequest
) -> PlannedGitHubIssueRun:
    """The run an ``@openswe`` mention on an issue starts on the channel's thread.

    The channel decided the mention is one and wrote the run's input; this checks
    the commenter and the public-repo gate again, since the channel's choice gives
    no access, indexes the thread and signs the run. While the thread waits it only
    says whether Open SWE acts.
    """
    payload = cast(_Payload, plan.payload)
    fields = {"github_event": plan.event, "github_delivery": plan.delivery_id}
    if plan.event == "issue_comment":
        sender_login = str((payload.get("sender") or {}).get("login") or "")
        if not await service.is_accepted_commenter(sender_login):
            return _no_run("Sender is not a registered Open SWE user", **fields)
    if not await _gated(payload, plan.event):
        return _no_run("The public-repo gate refused the sender", **fields)
    if request.waiting:
        # Nothing starts while the thread waits; no metadata either.
        return PlannedGitHubIssueRun(starts=True)
    run = await _prepare_run(plan, request)
    if run is None:
        return _no_run("no run prepared", **fields, agent_thread_id=str(request.thread_id))
    return PlannedGitHubIssueRun(starts=True, run=run)


async def github_issue_facts(
    _call: ChannelCall, request: GitHubIssueFactsRequest
) -> GitHubIssueFacts:
    """Who among the people an issue mention quotes is an Open SWE user, and what its
    thread already holds, for the channel to write the run's input."""
    thread_id = str(request.thread_id)
    started = await service.conversation_started(thread_id)
    return GitHubIssueFacts(
        conversation_started=started,
        sender_email_known=bool(await User.email_for_login(request.sender)),
        trusted=sorted(await User.known_logins(request.logins)),
        thread_workspace=await common.get_thread_workspace(thread_id) if started else None,
    )


async def github_automation_rules(
    _call: ChannelCall, request: GitHubAutomationRulesRequest
) -> GitHubAutomationRules:
    """The repository's enabled GitHub automations, for the channel to match a delivery to."""
    repository = request.repository.lower()
    records = await schedules.github_automation_rules(repository)
    if not records:
        return GitHubAutomationRules(automations=[])
    return GitHubAutomationRules(
        author_registered=bool(request.author and await User.known_logins([request.author])),
        automations=[
            GitHubAutomation(
                id=str(record["id"]),
                prompt=str(record["prompt"]),
                events=schedules.github_trigger_events(record, repository),
            )
            for record in records
        ],
    )


async def act_on_github_delivery(
    call: ChannelCall, act: ActOnGitHubDelivery
) -> GitHubDeliveryActed:
    """Plan the runs a delivery starts in the channel.

    The backend picks and runs its own work when the delivery is recorded. A channel
    deployed before that still names it here; a delivery's work runs once either way.
    """
    if act.work:
        await _accept_work(call, act, act.work)
    automations = await _plan_automations(act) if act.automations else []
    comment_run = await _route_comment(act) if act.comment else None
    issue_run = None if act.issue_run is None else await _plan_issue_run(act, act.issue_run)
    return GitHubDeliveryActed(
        issue_run=issue_run, automations=automations, comment_run=comment_run
    )


async def _plan_automations(act: ActOnGitHubDelivery) -> list[GitHubThreadRun]:
    """The runs of the automations the delivery fires; none when planning them fails,
    with a log line, so the delivery's comment and issue runs still start."""
    try:
        return await schedules.plan_github_automations(
            act.event, cast(_Payload, act.payload), act.delivery_id, act.automations
        )
    except Exception:
        logger.exception(
            "Planning GitHub automations failed; the delivery's other work goes on",
            extra={"github_delivery": act.delivery_id, "github_event": act.event},
        )
        return []


def _no_run(reason: str, **fields: str) -> PlannedGitHubIssueRun:
    """Answer the channel that the mention starts nothing, with one log line saying why."""
    logger.info(
        "MDA's GitHub channel starts no run",
        extra={"endpoint": "github", "no_run_reason": reason, **fields},
    )
    return PlannedGitHubIssueRun(starts=False)
