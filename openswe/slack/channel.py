"""What MDA's Slack channel asks the backend: the Slack conversations it handed to MDA.

Open SWE's Slack app keeps its URLs at ``/webhooks/slack``, which forwards the
conversations that belong to MDA to Trigger Server (``openswe.slack.mda_forwarding``).
The deployment's Slack channel owns those conversations' threads and starts their
runs; its handler asks ``plan_slack_turn`` (``runtime_api.slack``) what each message
starts. The backend gated each message when Slack delivered it and remembered the
request; here it builds the run from that request as it would for its own Slack
runs, with the signed run token, and says whether the message redirects the
thread's work in progress or waits its turn. ``plan_slack_click`` does the same
for a click on the agent's buttons.

``/slack/channel/events`` answers a deployment whose channel still names it as an
endpoint, until every deployment runs the handler.
"""

import logging
from datetime import timedelta
from typing import Annotated, Final, Literal, cast
from uuid import UUID, uuid7

from fastapi import APIRouter, Depends, Header, HTTPException
from managed_deepagents import EndpointAnswer, EndpointThread
from managed_deepagents import JsonValue as AnswerJson
from pydantic import JsonValue
from runtime_api.slack import (
    OptionClick,
    PlanSlackClick,
    PlanSlackTurn,
    RetryClick,
    SlackClickStart,
    SlackTurnRun,
    SlackTurnStart,
    WorkflowPushClick,
)
from typing_extensions import TypedDict

from openswe import event_claims
from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.mda_channels.api import ChannelCall
from openswe.mda_channels.auth import require_mda_channel
from openswe.slack import mda_forwarding
from openswe.slack import webhook as service
from openswe.slack.dm import CONCIERGE_TS
from openswe.slack.payloads import SlackChannelContext
from openswe.slack.request import SlackRequest
from openswe.slack.retry import SlackRetry
from openswe.threads import index
from openswe.threads.router import channel_run_metadata, channel_run_starting
from openswe.threads.runtimes import RUNTIMES
from openswe.webhooks import common

logger = logging.getLogger(__name__)

_MESSAGE_KEYS: Final = frozenset({"mention", "directMessage", "message"})
#: A clicked message is acted on once; the claim outlives its buttons.
_CLICK_SCOPE: Final = "slack_click"
_CLICK_TTL: Final = timedelta(days=7)
#: The call that claimed a message, which may be sent again.
_CLICK_CALL_SCOPE: Final = "slack_click_call"
_CLICK_CALL_TTL: Final = timedelta(minutes=10)
_WORKFLOW_PUSH_RETRY: Final = (
    "The workflow-file push approval was approved. Retry the blocked git push now; "
    "do not alter workflow files before pushing."
)

router = APIRouter(prefix="/slack/channel", dependencies=[Depends(require_mda_channel)])


class SlackChannelCall(TypedDict):
    """What MDA's Slack channel POSTs: the event (MDA's ``SlackEndpointEvent``) and its thread.

    The event stays a plain object here: its fields are read one by one below.
    """

    event: dict[str, JsonValue]
    thread: EndpointThread


def _no_run(reason: str, **fields: str) -> None:
    """Answer the channel that the event starts nothing, with one log line saying why."""
    logger.info(
        "MDA's Slack channel endpoint answers no run",
        extra={"endpoint": "slack", "no_run_reason": reason, **fields},
    )


@router.post("/events")
async def slack_channel_event(
    call: SlackChannelCall, webhook_id: Annotated[str, Header(alias="webhook-id")]
) -> EndpointAnswer | None:
    """The run a forwarded Slack message starts on the channel's thread, or ``None``.

    For a deployment whose channel names this endpoint; ``plan_slack_turn`` answers
    the handler that replaces it.
    """
    event, thread = call["event"], call["thread"]
    key = event.get("key")
    if key not in _MESSAGE_KEYS:
        # Button clicks in MDA-owned threads aren't forwarded yet.
        return _no_run("not a message", slack_channel_event=str(key))
    try:
        thread_id = str(UUID(thread["id"]))
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Invalid thread") from error
    message_ts = event.get("messageTs")
    if not isinstance(message_ts, str) or not message_ts:
        raise HTTPException(status_code=422, detail="The event names no message")
    run = (await _turn(thread_id, message_ts, webhook_id)).run
    if run is None:
        return None
    answer: EndpointAnswer = {
        "input": cast(dict[str, AnswerJson], run.input),
        "context": cast(dict[str, AnswerJson], run.context),
        "metadata": cast(dict[str, AnswerJson], run.metadata),
    }
    if run.webhook:
        answer["webhook"] = run.webhook
    return answer


async def plan_slack_turn(call: ChannelCall, plan: PlanSlackTurn) -> SlackTurnStart:
    """The run a forwarded Slack message starts, and whether it interrupts the thread's.

    No run when the message wasn't one the backend handed over (the backend gates
    every message before it forwards one), when gating posted the reply itself,
    such as the prompt to connect an account, or when building the run failed,
    which the Slack thread hears about. A call sent again under the same
    ``webhook-id`` gets the run built again.
    """
    webhook_id = call.request.headers.get("webhook-id", "")
    return await _turn(
        str(plan.thread_id),
        plan.message_ts,
        webhook_id,
        channel_id=plan.channel_id,
        thread_ts=plan.thread_ts,
    )


async def _turn(
    thread_id: str, message_ts: str, webhook_id: str, *, channel_id: str = "", thread_ts: str = ""
) -> SlackTurnStart:
    fields = {"agent_thread_id": thread_id, "slack_message_ts": message_ts}
    remembered = None
    for candidate in await _remembered_in(thread_id, channel_id, thread_ts):
        remembered = await mda_forwarding.recall(candidate, message_ts, webhook_id)
        if remembered is not None:
            break
    if remembered is None:
        logger.warning(
            "MDA's Slack channel asked about a message the backend didn't hand over",
            extra=fields,
        )
        _no_run("not handed over", **fields)
        return SlackTurnStart()
    request, repo = remembered
    run_thread = request.thread_id or thread_id
    try:
        start = await _prepare_run(run_thread, request, repo)
    except Exception as exc:  # noqa: BLE001 - the person hears about it instead
        # The request was taken: nothing else will tell the Slack thread.
        await service.notify_slack_processing_error(request, repo.repo if repo else None, exc)
        _no_run("building the run failed", **fields)
        return SlackTurnStart()
    if start.run is None:
        _no_run("no run prepared", **fields)
    elif run_thread != thread_id:
        start.thread_id = UUID(run_thread)
    return start


async def _remembered_in(thread_id: str, channel_id: str, thread_ts: str) -> list[str]:
    """The Slack thread's own thread, then a concierge DM's it is bound to."""
    threads = [thread_id]
    if channel_id and thread_ts:
        owner = await mda_forwarding.owned_conversation(channel_id, thread_ts)
        if owner is not None and owner not in threads:
            threads.append(owner)
    return threads


async def plan_slack_click(call: ChannelCall, plan: PlanSlackClick) -> SlackClickStart:
    """The run a click starts, once per clicked message; the handler checked who clicked."""
    fields = {"slack_channel_id": plan.channel_id, "slack_message_ts": plan.message_ts}
    run_thread = await mda_forwarding.owned_conversation(plan.channel_id, plan.thread_ts)
    if run_thread is None:
        logger.warning("MDA's Slack channel asked about a click outside its threads", extra=fields)
        return SlackClickStart(acted=False)
    channel_context = await common.resolve_slack_channel_context(plan.channel_id, use_cache=False)
    if not channel_context.allows_operations:
        return SlackClickStart(acted=False)
    claimed = await _claim_click(plan, call.request.headers.get("webhook-id", ""))
    if claimed is None:
        _no_run("clicked before", **fields)
        return SlackClickStart(acted=False)
    concierge = plan.channel_id.startswith("D") and run_thread == (
        await mda_forwarding.owned_thread(plan.channel_id, CONCIERGE_TS)
    )
    click = plan.click
    status: str | None = None
    if isinstance(click, WorkflowPushClick):
        decision = await common.decide_workflow_push_approval(
            run_thread, click.fingerprint, approved=click.approved, actor=plan.user_id
        )
        if decision is None:
            return SlackClickStart(workflow_push=None)
        record, changed = decision
        status = str(record.get("status"))
        if status not in {"approved", "rejected"}:
            return SlackClickStart(workflow_push=None)
        if not changed and claimed == "first":
            # Decided before this message's buttons could: nothing to start again.
            return SlackClickStart(acted=False, workflow_push=status)
        if status == "rejected":
            return SlackClickStart(workflow_push="rejected")
    start = SlackClickStart(workflow_push="approved" if status == "approved" else None)
    request = await _click_request(plan, click, run_thread, channel_context, concierge=concierge)
    repo = await common.get_slack_repo_config(
        plan.channel_id,
        request.thread_ts,
        slack_user_id=plan.user_id,
        channel_context=channel_context,
        thread_id=run_thread,
    )
    try:
        turn = await _prepare_run(run_thread, request, repo)
    except Exception as exc:  # noqa: BLE001 - the person hears about it instead
        await service.notify_slack_processing_error(request, repo.repo if repo else None, exc)
        _no_run("building the run failed", **fields)
        return start
    start.run, start.interrupts = turn.run, turn.interrupts
    if turn.run is not None and run_thread != str(plan.thread_id):
        start.thread_id = UUID(run_thread)
    return start


async def _claim_click(plan: PlanSlackClick, webhook_id: str) -> Literal["first", "again"] | None:
    """``first`` or ``again`` (the same webhook ID) when this call acts on the message."""
    message = f"{plan.channel_id}:{plan.message_ts}"
    call = f"{webhook_id}:{message}"
    if webhook_id and await event_claims.held(_CLICK_CALL_SCOPE, call):
        return "again"
    if not await event_claims.claim(_CLICK_SCOPE, message, ttl=_CLICK_TTL):
        return None
    if webhook_id:
        await event_claims.claim(_CLICK_CALL_SCOPE, call, ttl=_CLICK_CALL_TTL)
    return "first"


async def _click_request(
    plan: PlanSlackClick,
    click: OptionClick | WorkflowPushClick | RetryClick,
    run_thread: str,
    channel_context: SlackChannelContext,
    *,
    concierge: bool,
) -> SlackRequest:
    if isinstance(click, RetryClick):
        return await SlackRetry(
            channel_id=plan.channel_id,
            thread_ts=click.thread_ts,
            user_id=plan.user_id,
            message_ts=click.message_ts,
            reply_thread_ts=click.reply_thread_ts,
            nonce="",
        ).request(thread_id=run_thread, channel_context=channel_context, team_id=plan.team_id)
    # A concierge DM answers a click inside a Slack thread there.
    reply_thread_ts = plan.thread_ts if concierge and plan.thread_ts != plan.message_ts else ""
    return SlackRequest(
        channel_id=plan.channel_id,
        channel_context=channel_context,
        thread_ts=CONCIERGE_TS if concierge else plan.thread_ts,
        event_ts=plan.action_ts,
        event_id=f"slack-click:{uuid7()}",
        user_id=plan.user_id,
        text=click.text if isinstance(click, OptionClick) else _WORKFLOW_PUSH_RETRY,
        bot_user_id=common.SLACK_BOT_USER_ID,
        thread_id=run_thread,
        concierge_mode=concierge,
        reply_thread_ts=reply_thread_ts,
        team_id=plan.team_id,
    )


async def _prepare_run(
    thread_id: str, request: SlackRequest, repo: common.SlackRepoResolution | None
) -> SlackTurnStart:
    prepared = await service.prepare_slack_channel_run(request, repo)
    if prepared is None:
        return SlackTurnStart()

    run_config = await prepare_durable_run(
        "agent",
        {"configurable": prepared.configurable},
        {
            **common.AGENT_VERSION_METADATA,
            CHANNEL_METADATA_KEY: mda_forwarding.CHANNEL,
            "slack_trigger_ts": prepared.trigger_ts,
        },
        source="slack",
    )
    indexed = await index.get(thread_id)
    rewritten = await RUNTIMES.mda_runtime.run_payload(
        thread_id,
        {
            "assistant_id": "agent",
            "config": cast(JsonValue, run_config),
            # So the attribution the run's hooks read rides in its run token.
            "metadata": cast(JsonValue, run_config["metadata"]),
        },
        thread_metadata=indexed.metadata if indexed else {},
    )
    run = SlackTurnRun(
        input=cast(dict[str, JsonValue], prepared.input),
        context=cast(dict[str, JsonValue], rewritten["context"]),
        metadata=channel_run_metadata(run_config["metadata"]),
        webhook=COMPLETION_WEBHOOK_URL or None,
    )
    await channel_run_starting(thread_id)
    return SlackTurnStart(run=run, interrupts=prepared.interrupts)
