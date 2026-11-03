"""What MDA's Slack channel asks the backend about the Slack conversations it holds.

The deployment's Slack channel decides what each message starts and builds its
prompt from Slack (``rt.slack``). It asks here only for what needs Open SWE's
records (``runtime_api.slack``): the facts deciding a message needs
(``slack_message_facts``), then the turn (``start_slack_turn``). The backend gates
the turn, makes a new conversation MDA's, records the thread, signs the run, and
says whether the message redirects the thread's work in progress or waits its turn,
or why it starts nothing, which the channel's handler replies with: a person who
must sign in first. ``plan_slack_click`` does the same for a click on the agent's
buttons.
"""

import json
import logging
from datetime import timedelta
from typing import Any, Final, Literal, cast
from uuid import UUID, uuid7

from fastapi import HTTPException
from pydantic import JsonValue
from runtime_api.slack import (
    SLACK_MOVE_MARKER,
    ActOnSlackClick,
    OptionClick,
    PlanSlackClick,
    RetryClick,
    SlackClickActed,
    SlackClickStart,
    SlackMessage,
    SlackMessageFacts,
    SlackMessageFactsRequest,
    SlackTurnDeclined,
    SlackTurnRun,
    SlackTurnStarted,
    StartSlackTurn,
    WorkflowPushClick,
)

from openswe import event_claims
from openswe.dashboard.oauth import build_settings_url
from openswe.dispatch import CHANNEL_METADATA_KEY, COMPLETION_WEBHOOK_URL, prepare_durable_run
from openswe.input_messages import RunInput
from openswe.mda_channels.api import ChannelCall
from openswe.slack import forks, mda_forwarding
from openswe.slack import webhook as service
from openswe.slack.allowed_bots import resolve_allowed_slack_bot
from openswe.slack.ask import by_the_way_request, prepare_ask_channel_run
from openswe.slack.breakout import BreakoutCommand, process_slack_breakout, web_opening
from openswe.slack.breakout_links import notify_web_breakout
from openswe.slack.client import SlackThreadMappingError
from openswe.slack.dm import CONCIERGE_TS, is_dm_channel
from openswe.slack.parsed_message import ParsedSlackMessage, SlackAction
from openswe.slack.payloads import SlackChannelContext, SlackInteraction
from openswe.slack.records import lookup_slack_thread_id
from openswe.slack.request import SlackRequest
from openswe.slack.retry import SlackRetry
from openswe.slack.solo_threads import allow_solo_thread_followup, track_explicit_mention
from openswe.slack.webhook import SlackTurnRead
from openswe.threads import index
from openswe.threads.router import channel_run_metadata, channel_run_starting
from openswe.threads.runtimes import RUNTIMES
from openswe.users import User
from openswe.utils.thread_ops import langgraph_client as get_langgraph_client
from openswe.webhooks import common
from openswe.workspaces.routing import is_kitchen_channel
from openswe.workspaces.store import WORKSPACES

logger = logging.getLogger(__name__)

#: A clicked message is acted on once; the claim outlives its buttons.
_CLICK_SCOPE: Final = "slack_click"
_CLICK_TTL: Final = timedelta(days=7)
#: The call that claimed a message, which may be sent again.
_CLICK_CALL_SCOPE: Final = "slack_click_call"
_CLICK_CALL_TTL: Final = timedelta(minutes=10)
#: A turn's decline is answered once per call; the claim outlives the call's retries.
_DECLINE_SCOPE: Final = "slack_turn_decline"
_DECLINE_TTL: Final = timedelta(minutes=10)
#: Claims for the notice a ``/breakout:web`` posts in its Slack thread, one per call.
_WEB_BREAKOUT_SCOPE: Final = "slack_web_breakout_notice"
_WORKFLOW_PUSH_RETRY: Final = (
    "The workflow-file push approval was approved. Retry the blocked git push now; "
    "do not alter workflow files before pushing."
)


def _no_run(reason: str, **fields: str) -> None:
    """Answer the channel that the event starts nothing, with one log line saying why."""
    logger.info(
        "MDA's Slack channel starts no run",
        extra={"endpoint": "slack", "no_run_reason": reason, **fields},
    )


async def slack_message_facts(
    _call: ChannelCall, message: SlackMessageFactsRequest
) -> SlackMessageFacts:
    """What deciding a Slack message needs from Open SWE's records, as Slack has it now.

    While Slack can't confirm the channel has no external participants the call fails
    with a 503, so it is sent again.
    """
    channel_context = await common.resolve_slack_channel_context(
        message.channel_id, use_cache=False
    )
    if not channel_context.allows_operations:
        return SlackMessageFacts(channel=_refused(channel_context, message.channel_id))
    dm = message.channel_type == "im" or is_dm_channel(channel_context)
    code_channel = await common.is_code_channel(message.channel_id)
    allowed_bot = (
        await resolve_allowed_slack_bot(
            message.team_id, message.bot_id, user_id=message.user_id, app_id=message.bot_app_id
        )
        if message.bot_id
        else None
    )
    kitchen = not (code_channel or dm) and await is_kitchen_channel(message.channel_id)
    concierge = dm and not code_channel and await User.concierge_mode_for_slack(message.user_id)
    parsed = ParsedSlackMessage.parse(
        message.text, common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME
    )
    solo_followup = False
    if (
        parsed.action is not SlackAction.BY_THE_WAY
        and not (code_channel or dm or message.bot_id)
        and message.subtype in {"", "file_share", "thread_broadcast"}
        and (message.reply_thread_ts or message.explicit_mention)
    ):
        if message.explicit_mention:
            track_explicit_mention(
                get_langgraph_client(),
                channel_id=message.channel_id,
                thread_ts=message.thread_ts,
                message_ts=message.message_ts,
                user_id=message.user_id,
            )
        else:
            solo_followup = await allow_solo_thread_followup(
                get_langgraph_client(),
                channel_id=message.channel_id,
                thread_ts=message.thread_ts,
                message_ts=message.message_ts,
                user_id=message.user_id,
                explicit_mention=False,
            )
    return SlackMessageFacts(
        dm=dm,
        code_channel=code_channel,
        kitchen_channel=kitchen,
        concierge=concierge,
        allowed_bot=allowed_bot is not None,
        bot_name=(allowed_bot.name.strip() or "Bot") if allowed_bot is not None else "",
        solo_followup=solo_followup,
        conversation=await _conversation(message, CONCIERGE_TS if concierge else message.thread_ts),
        workspace_known=bool(message.workspace_tag)
        and await WORKSPACES.get(message.workspace_tag) is not None,
    )


def _refused(channel: SlackChannelContext, channel_id: str) -> Literal["external", "unverified"]:
    """Why Open SWE doesn't operate in a channel; a 503 while Slack can't confirm it yet."""
    if channel.is_ext_shared is True:
        return "external"
    if channel.is_pending_ext_shared is not True and channel.lookup_retry_after is not None:
        logger.warning(
            "Slack channel verification unavailable; MDA's Slack channel asks again",
            extra={"slack_channel_id": channel_id},
        )
        raise HTTPException(
            status_code=503,
            detail="Slack channel verification is temporarily unavailable.",
            headers={"Retry-After": str(channel.lookup_retry_after)},
        )
    return "unverified"


async def _conversation(message: SlackMessage, root_ts: str) -> Literal["mda", "backend", "new"]:
    """Who holds the conversation of the Slack thread at ``root_ts``."""
    if await mda_forwarding.owned_thread(message.channel_id, root_ts) is not None:
        return "mda"
    if await lookup_slack_thread_id(message.channel_id, root_ts) is not None:
        return "backend"
    # A conversation that moved to another Slack thread took its thread along, and MDA
    # handed it to that Slack thread, so this one starts on the backend again.
    moved = mda_forwarding.mda_thread_id(
        message.app_id, message.team_id, message.channel_id, root_ts
    )
    return "backend" if await index.get(moved) is not None else "new"


async def start_slack_turn(call: ChannelCall, turn: StartSlackTurn) -> SlackTurnStarted:
    """The run a message MDA's Slack channel decided on starts, and whether it interrupts.

    A new conversation becomes MDA's here, once its sender passed the account gate. For
    a ``turn`` the channel built the prompt from Slack: the run's input is empty, and
    ``prompt`` says what the prompt needs from Open SWE's records. A ``/btw`` question, a
    breakout and a move are built whole, as the backend always built them. A call sent
    again under the same ``webhook-id`` gets the run built again, but no decline it
    answered already.
    """
    webhook_id = call.request.headers.get("webhook-id", "")
    fields = {"agent_thread_id": str(turn.thread_id), "slack_message_ts": turn.message_ts}
    channel_context = await common.resolve_slack_channel_context(turn.channel_id)
    key_ts = CONCIERGE_TS if turn.concierge else turn.thread_ts
    owner_login = None if turn.bot_id else await service.slack_login(turn.user_id)
    parsed = ParsedSlackMessage.parse(
        turn.text, common.SLACK_BOT_USER_ID, common.SLACK_BOT_USERNAME
    )
    branch_of = ""
    if turn.kind in {"by_the_way", "web"}:
        # On a thread of its own, whoever holds the Slack thread it was asked in. It starts
        # with what the conversation's thread holds when this channel may read it, or is
        # seeded from the Slack thread.
        source = await mda_forwarding.owned_thread(turn.channel_id, key_ts)
        if await _branches(source, turn, owner_login):
            branch_of = str(source)
        conversation = mda_forwarding.side_thread_id(turn.channel_id, turn.message_ts)
        await mda_forwarding.claim_side(conversation, owner_login=owner_login)
    elif (owned := await mda_forwarding.owned_thread(turn.channel_id, key_ts)) is not None:
        conversation = owned
    elif turn.kind == "breakout":
        # Opens its own Slack thread, seeded from this one, whoever holds it.
        try:
            conversation = await common.resolve_slack_thread_id(
                get_langgraph_client(), turn.channel_id, key_ts
            )
        except SlackThreadMappingError as exc:
            raise HTTPException(
                status_code=409, detail="The Slack thread belongs to another thread"
            ) from exc
    elif turn.kind == "move":
        # Only a conversation this channel holds moves through MDA.
        _no_run("a move outside an MDA conversation", **fields)
        return SlackTurnStarted()
    else:
        declined = await _account_gate(turn, webhook_id, fields)
        if declined is not None:
            return declined
        conversation = await _claim(turn, owner_login)
    if turn.concierge:
        # Each Slack thread of a concierge DM joins the DM's one conversation.
        await mda_forwarding.join_concierge(turn.channel_id, turn.thread_ts, conversation)
    request = SlackRequest(
        channel_id=turn.channel_id,
        channel_context=channel_context,
        thread_ts=key_ts,
        event_ts=turn.message_ts,
        original_message_ts=turn.message_ts,
        retry_message_ts=turn.message_ts,
        # One per message, so a call sent again claims nothing twice.
        event_id=f"mda-slack:{turn.channel_id}:{turn.message_ts}",
        user_id=turn.user_id,
        text=turn.text,
        attachments=cast(list[dict[str, Any]], turn.attachments),
        bot_user_id=common.SLACK_BOT_USER_ID,
        thread_id=conversation,
        held_thread_id=branch_of,
        treat_all_messages_as_mentions=turn.treat_all_messages_as_mentions,
        kitchen_channel=turn.kitchen_channel,
        explicit_mention=turn.explicit_mention,
        concierge_mode=turn.concierge,
        reply_thread_ts=turn.reply_thread_ts if turn.concierge else "",
        team_id=turn.team_id,
        triggering_bot_id=turn.bot_id,
        triggering_bot_app_id=turn.bot_app_id if turn.bot_id else "",
    )
    repo = (
        None
        if turn.kind == "by_the_way"
        else await common.get_slack_repo_config(
            turn.channel_id,
            key_ts,
            slack_user_id=turn.user_id,
            channel_context=channel_context,
            thread_id=conversation,
        )
    )
    read: SlackTurnRead | None = None
    match turn.kind:
        case "by_the_way":
            request = request.model_copy(update={"by_the_way": parsed.argument})
        case "web":
            request = await web_opening(request, BreakoutCommand.from_message(parsed), conversation)
        case "breakout":
            branches = await _branches(conversation, turn, owner_login)
            opened = await _open_breakout(
                request, BreakoutCommand.from_message(parsed), repo, branches=branches
            )
            if opened is None:
                _no_run("breakout opened nothing", **fields)
                return SlackTurnStarted()
            request, repo = opened
        case "move":
            request = request.model_copy(
                update={"move_thread": True, "move_channel_id": turn.move_channel_id}
            )
        case "turn":
            read = SlackTurnRead(
                clean_text=turn.clean_text,
                context_user_ids=turn.context_user_ids,
                participant_user_ids=turn.participant_user_ids,
                has_images=turn.has_images,
                review_card=turn.review_card,
            )
    run_thread = request.thread_id or conversation
    try:
        start = await _prepare_run(run_thread, request, repo, declines=True, read=read)
    except Exception as exc:  # noqa: BLE001 - the person hears about it instead
        await service.notify_slack_processing_error(request, repo.repo if repo else None, exc)
        _no_run("building the run failed", **fields)
        return SlackTurnStarted()
    if start.declined is not None:
        if not await _first_decline(webhook_id, turn):
            _no_run("declined already", **fields)
            return SlackTurnStarted()
        _no_run(f"declined: {start.declined.reason}", **fields)
    elif start.run is None:
        _no_run("no run prepared", **fields)
    elif run_thread != str(turn.thread_id):
        start.thread_id = UUID(run_thread)
        # The channel starts the new thread with what its conversation's thread holds.
        start.branches_conversation = bool(request.held_thread_id)
    if start.run is not None and request.web_only:
        await _notify_web_breakout(request, webhook_id)
    return start


async def _account_gate(
    turn: StartSlackTurn, webhook_id: str, fields: dict[str, str]
) -> SlackTurnStarted | None:
    """Why a person who can't run starts no new conversation, before it is claimed, so it
    stays unclaimed for whoever can. An allowed bot links no account."""
    if turn.bot_id:
        return None
    needed = await service.account_link_needed(turn.user_id)
    if needed is None:
        return None
    if not await _first_decline(webhook_id, turn):
        _no_run("declined already", **fields)
        return SlackTurnStarted()
    _no_run("declined: sign in", **fields)
    return SlackTurnStarted(
        declined=SlackTurnDeclined(
            reason="sign_in_again" if needed.reason == "revoked" else "sign_in",
            settings_url=build_settings_url(),
        )
    )


async def _first_decline(webhook_id: str, turn: StartSlackTurn) -> bool:
    """Whether this call answers its message's decline: once per call, across its retries."""
    decline = f"{webhook_id}:{turn.thread_id}:{turn.message_ts}"
    return await event_claims.claim(_DECLINE_SCOPE, decline, ttl=_DECLINE_TTL)


async def _claim(turn: StartSlackTurn, owner_login: str | None) -> str:
    """Make a new conversation MDA's channel's: its thread."""
    try:
        if turn.concierge:
            return await mda_forwarding.claim_concierge(
                turn.app_id, turn.team_id, turn.channel_id, owner_login=owner_login
            )
        return await mda_forwarding.claim_conversation(
            turn.app_id, turn.team_id, turn.channel_id, turn.thread_ts, owner_login=owner_login
        )
    except SlackThreadMappingError as exc:
        raise HTTPException(
            status_code=409, detail="The Slack thread belongs to another thread"
        ) from exc


async def _branches(source: object, turn: StartSlackTurn, owner_login: str | None) -> bool:
    """Whether a branch's new thread starts with what ``source`` holds: the thread this
    turn's channel handler can read (its event's conversation), which the person may read."""
    return (
        source is not None
        and str(source) == str(turn.thread_id)
        and await forks.branches_from(str(source), login=owner_login)
    )


async def _open_breakout(
    request: SlackRequest,
    command: BreakoutCommand,
    repo: common.SlackRepoResolution | None,
    *,
    branches: bool,
) -> tuple[SlackRequest, common.SlackRepoResolution | None] | None:
    """Open a breakout's new Slack thread: its first turn, which runs on the command's own
    thread; ``None`` when the breakout told the sender why it opened nothing."""
    opened: list[tuple[SlackRequest, common.SlackRepoResolution | None]] = []

    async def hand_to_mda(
        opening: SlackRequest, opening_repo: common.SlackRepoResolution | None
    ) -> None:
        opened.append((opening, opening_repo))

    await process_slack_breakout(request, command, repo, hand_to_mda=hand_to_mda, branches=branches)
    return opened[0] if opened else None


async def _notify_web_breakout(request: SlackRequest, webhook_id: str) -> None:
    """Tell the Slack thread a ``/breakout:web`` came from where it continues, once."""
    notice = f"{webhook_id}:{request.thread_id}"
    if await event_claims.claim(_WEB_BREAKOUT_SCOPE, notice, ttl=_DECLINE_TTL):
        await notify_web_breakout(request)


async def plan_slack_click(call: ChannelCall, plan: PlanSlackClick) -> SlackClickStart:
    """The run a click starts, once per clicked message; the handler checked who clicked."""
    fields = {"slack_channel_id": plan.channel_id, "slack_message_ts": plan.message_ts}
    run_thread = await mda_forwarding.owned_conversation(plan.channel_id, plan.thread_ts)
    if run_thread is None:
        return SlackClickStart(acted=False, backend=True)
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


async def act_on_slack_click(call: ChannelCall, click: ActOnSlackClick) -> SlackClickActed:
    """Act on a click as the backend always has, as the person Trigger Server verified."""
    webhook_id = call.request.headers.get("webhook-id", "")
    if webhook_id and not await event_claims.claim(
        _CLICK_CALL_SCOPE, f"act:{webhook_id}", ttl=_CLICK_CALL_TTL
    ):
        return SlackClickActed(acted=False)
    payload = dict(click.payload)
    user = payload.get("user")
    payload["user"] = {**(user if isinstance(user, dict) else {}), "id": click.user_id}
    interaction = SlackInteraction.parse(payload)
    if interaction is None:
        return SlackClickActed(acted=False)
    from openswe.slack.routes import act_on_interaction  # noqa: PLC0415 - routes imports this

    await act_on_interaction(
        interaction,
        json.dumps(payload),
        call.background,
        origin=(click.channel_id, click.thread_ts),
    )
    return SlackClickActed()


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


def _mark_move(run_input: RunInput, channel_id: str) -> None:
    """Have the run move its thread before it calls the model: a bare ``/breakout``, into
    ``channel_id`` when it names another Slack channel.

    MDA's ``rt.slack_move`` moves it and ends the run; what it posts and where it goes,
    the backend says as the run (``slack_move``).
    """
    messages = run_input["messages"]
    if messages:
        messages[-1].setdefault("additional_kwargs", {})[SLACK_MOVE_MARKER] = channel_id or True


async def _prepare_by_the_way(request: SlackRequest) -> service.SlackChannelRun | None:
    """A `/btw` question's run on its own thread. Its refusals and the prompt to connect an
    account reach the asker privately, as on the backend, so it never declines."""
    asked = await prepare_ask_channel_run(by_the_way_request(request))
    if asked is None:
        return None
    return service.SlackChannelRun(
        input=asked.input,
        configurable=cast(dict[str, JsonValue], asked.configurable),
        trigger_ts=request.original_message_ts,
        interrupts=False,
    )


async def _prepare_run(
    thread_id: str,
    request: SlackRequest,
    repo: common.SlackRepoResolution | None,
    *,
    declines: bool = False,
    read: SlackTurnRead | None = None,
) -> SlackTurnStarted:
    prompt = None
    if request.by_the_way is not None:
        prepared = await _prepare_by_the_way(request)
    else:
        prepared, prompt = await service.prepare_slack_channel_run(
            request, repo, declines=declines, read=read
        )
    if prepared is None:
        return SlackTurnStarted()
    if isinstance(prepared, SlackTurnDeclined):
        return SlackTurnStarted(declined=prepared)
    if request.move_thread:
        _mark_move(prepared.input, request.move_channel_id)

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
    return SlackTurnStarted(run=run, interrupts=prepared.interrupts, prompt=prompt)
