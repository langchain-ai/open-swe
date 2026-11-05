"""What Open SWE's GitHub channel does with each GitHub delivery, in this deployment.

Every delivery goes to the backend's event log (``record_github_delivery``), which
answers what Open SWE knows about its repository. This side decides what the
delivery starts (``rt.github_routes``), and tells the backend
(``act_on_github_delivery``): first, in a call of its own, the backend's work,
such as a review or a CI wakeup, which the backend runs once per delivery; then
the automations it fires, a pull request comment to route, and for an
``@openswe`` mention on an issue, the coding agent's run on the issue's
conversation. An automation that fails fires nothing and stops nothing else, as
when the backend ran them in the background. This side writes the runs' prompts: each
automation's (``rt.github_automations``, from the repository's rules,
``github_automation_rules``) and the issue's (``rt.github_issues``, from who is an
Open SWE user, ``github_issue_facts``). The backend checks and signs the runs, and
this side starts them: each automation's on a thread of its own, a comment's on the
pull request's own thread, the issue's on its conversation. A comment's run on the
agent's own thread the backend still writes and starts itself: it reads the pull
request's comments as the person and filters a private thread's to its owner's. So
a delivery takes a call or two, and one that fires automations or mentions Open
SWE on an issue a call or two more. An installation event, sent when the App is
installed, removed or suspended or its repositories change, is logged the same way
and drops the backend's cached repository listings.
"""

import asyncio
import logging
import re
from typing import Final, cast
from uuid import UUID

from langgraph_sdk.schema import MultitaskStrategy
from managed_deepagents import (
    ChannelRun,
    GitHubChannelThread,
    GitHubEvent,
    GitHubEventResult,
    GitHubInstallationEvent,
    GitHubPayload,
)
from pydantic import JsonValue
from runtime_api.github import (
    ACT_ON_GITHUB_DELIVERY,
    GITHUB_AUTOMATION_RULES,
    GITHUB_ISSUE_FACTS,
    RECORD_GITHUB_DELIVERY,
    REPOSITORY_PATTERN,
    ActOnGitHubDelivery,
    FiredGitHubAutomation,
    GitHubAutomationRulesRequest,
    GitHubDelivery,
    GitHubIssueFactsRequest,
    GitHubIssueRunRequest,
    GitHubThreadRun,
    PlannedGitHubIssueRun,
)

from rt.github_automations import SubjectEvent
from rt.github_issues import IssueMention, is_login
from rt.github_routes import route
from rt.input_messages import RunInput
from rt.intake import ask_backend

logger = logging.getLogger(__name__)
_REPOSITORY: Final = re.compile(REPOSITORY_PATTERN)

# What a mention starts while the issue's thread waits on an interrupt: the run
# doesn't start, the channel only points the issue to the decision.
ACTS_WHILE_WAITING: Final = "Open SWE was mentioned while the thread waits."


async def on_event[Payload: GitHubPayload](
    event: GitHubEvent[Payload], thread: GitHubChannelThread
) -> GitHubEventResult:
    """Log the delivery, start the backend's work it routes to, its automations and an
    issue's run."""
    delivery = GitHubDelivery(
        event=event.name,
        delivery_id=event.delivery_id,
        payload=cast(dict[str, JsonValue], event.payload),
    )
    facts = await ask_backend(
        RECORD_GITHUB_DELIVERY, delivery, msg_id=f"msg_{event.delivery_id}_record"
    )
    routed = route(event.name, delivery.payload, facts, from_app=event.from_app)
    if routed.work:
        # The backend's own work goes first, in a call of its own: nothing planned
        # after it, such as an automation that fails, keeps a review from running.
        await ask_backend(
            ACT_ON_GITHUB_DELIVERY,
            ActOnGitHubDelivery(**delivery.model_dump(), work=list(routed.work)),
            msg_id=f"msg_{event.delivery_id}_work",
        )
    mention = IssueMention.of(event.name, delivery.payload) if routed.issue_run else None
    issue_run, automations = await asyncio.gather(
        _issue_run_request(event.delivery_id, mention, thread),
        _automations(delivery, f"{event.repository.owner}/{event.repository.name}")
        if routed.automations
        else _none(),
    )
    if not (routed.comment or automations or issue_run):
        return None
    acted = await ask_backend(
        ACT_ON_GITHUB_DELIVERY,
        ActOnGitHubDelivery(
            **delivery.model_dump(),
            comment=routed.comment,
            mentioned=routed.mentioned,
            automations=automations,
            issue_run=issue_run,
        ),
        msg_id=f"msg_{event.delivery_id}_act",
    )
    runs: list[ChannelRun] = []
    if mention is not None and issue_run is not None and acted.issue_run is not None:
        runs += await _issue_runs(mention, issue_run, acted.issue_run)
    runs += [_thread_run(run) for run in acted.automations]
    if acted.comment_run is not None:
        # A newer comment replaces the work on an older one, as the backend's dispatch did.
        runs.append(_thread_run(acted.comment_run, multitask_strategy="interrupt"))
    return runs or None


async def _none() -> list[FiredGitHubAutomation]:
    return []


async def _automations(delivery: GitHubDelivery, repository: str) -> list[FiredGitHubAutomation]:
    """The repository's automations the delivery fires, each with its run's prompt.

    One that can't be worked out fires nothing, with a log line saying so: the
    delivery's other work, such as a mention's run, goes on without it.
    """
    subject = SubjectEvent.of(delivery.event, delivery.payload)
    if subject is None or not _REPOSITORY.fullmatch(repository):
        return []
    try:
        rules = await ask_backend(
            GITHUB_AUTOMATION_RULES,
            GitHubAutomationRulesRequest(repository=repository, author=subject.author[:64]),
            msg_id=f"msg_{delivery.delivery_id}_automations",
        )
        return subject.fired(rules)
    except Exception:
        logger.exception(
            "GitHub automations failed; the delivery's other work goes on",
            extra={"github_delivery": delivery.delivery_id, "github_event": delivery.event},
        )
        return []


async def _issue_run_request(
    delivery_id: str, mention: IssueMention | None, thread: GitHubChannelThread
) -> GitHubIssueRunRequest | None:
    """The issue's run, written, for the backend to check and sign; ``None`` when the
    mention starts nothing."""
    if mention is None:
        return None
    thread_id = UUID(thread.id)
    if thread.interrupted:
        # Nothing starts while the thread waits: the backend only says whether Open SWE acts.
        return GitHubIssueRunRequest(thread_id=thread_id, waiting=True)
    if not is_login(mention.sender):
        return _no_run("The delivery names no sender", delivery_id)
    asked = mention.logins()
    facts = await ask_backend(
        GITHUB_ISSUE_FACTS,
        GitHubIssueFactsRequest(thread_id=thread_id, sender=mention.sender, logins=asked),
        msg_id=f"msg_{delivery_id}_issue",
    )
    trusted = frozenset(facts.trusted)
    if mention.event == "issue_comment" and mention.sender.lower() not in trusted:
        return _no_run("Sender is not a registered Open SWE user", delivery_id)
    if not facts.sender_email_known:
        return _no_run("No email mapping for the sender", delivery_id)
    if facts.conversation_started:
        # A follow-up stays in the workspace its thread started in.
        return _request(thread_id, mention.follow_up(trusted), facts.thread_workspace)
    comments = mention.with_comment(await mention.comments())
    if unasked := [login for login in mention.logins(comments) if login not in asked]:
        trusted |= frozenset(
            (
                await ask_backend(
                    GITHUB_ISSUE_FACTS,
                    GitHubIssueFactsRequest(
                        thread_id=thread_id, sender=mention.sender, logins=unasked
                    ),
                    msg_id=f"msg_{delivery_id}_issue_comments",
                )
            ).trusted
        )
    return _request(thread_id, mention.first(comments, trusted), None)


def _request(thread_id: UUID, run_input: RunInput, workspace: str | None) -> GitHubIssueRunRequest:
    return GitHubIssueRunRequest(
        thread_id=thread_id,
        input=cast(dict[str, JsonValue], run_input),
        workspace=workspace,
    )


def _no_run(reason: str, delivery_id: str) -> None:
    """The mention starts nothing; one log line says why."""
    logger.info(
        "MDA's GitHub channel starts no issue run",
        extra={"no_run_reason": reason, "github_delivery": delivery_id},
    )


async def _issue_runs(
    mention: IssueMention, request: GitHubIssueRunRequest, planned: PlannedGitHubIssueRun
) -> list[ChannelRun]:
    """The issue's run on its conversation, or, while it waits, what points it to the decision."""
    if not planned.starts:
        return []
    if planned.run is None or request.input is None:
        return [{"input": ACTS_WHILE_WAITING}]
    await mention.acknowledge()
    run: ChannelRun = {
        "input": request.input,
        "context": planned.run.context,
        "metadata": planned.run.metadata,
    }
    if planned.run.webhook:
        run["webhook"] = planned.run.webhook
    return [run]


async def on_installation_event(
    event: GitHubInstallationEvent, _thread: GitHubChannelThread
) -> GitHubEventResult:
    """Log the delivery, and start the backend's work it routes to; it starts no run."""
    delivery = GitHubDelivery(
        event=event.name,
        delivery_id=event.delivery_id,
        payload=cast(dict[str, JsonValue], event.payload),
    )
    facts = await ask_backend(
        RECORD_GITHUB_DELIVERY, delivery, msg_id=f"msg_{event.delivery_id}_record"
    )
    routed = route(event.name, delivery.payload, facts, from_app=False)
    if routed.work:
        await ask_backend(
            ACT_ON_GITHUB_DELIVERY,
            ActOnGitHubDelivery(**delivery.model_dump(), work=list(routed.work)),
            msg_id=f"msg_{event.delivery_id}_act",
        )
    return None


def _thread_run(
    run: GitHubThreadRun, *, multitask_strategy: MultitaskStrategy | None = None
) -> ChannelRun:
    """A run on the thread the backend planned it on: an automation's, or the pull request's."""
    started: ChannelRun = {
        "input": run.input,
        "context": run.context,
        "metadata": run.metadata,
        "thread": {"mode": "persistent", "id": str(run.thread_id)},
    }
    if run.webhook:
        started["webhook"] = run.webhook
    if multitask_strategy is not None:
        started["multitask_strategy"] = multitask_strategy
    return started
