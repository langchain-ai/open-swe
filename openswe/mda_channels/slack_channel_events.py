"""A Slack channel's own changes, and reactions, which MDA's Slack channel hands the backend.

Where Slack's Events URL points at Trigger Server, the backend no longer hears a
channel's creation, rename, archive, unarchive or deletion, nor a reaction. MDA's
Slack channel takes them with its ``channel`` and ``reaction`` handlers
(``mda/channels/slack.py``) and POSTs them here, signed with ``MDA_SERVICE_SECRET``.
The backend does what ``/webhooks/slack`` does with them: it keeps the channel
directory, incident channels and review guides, records a feedback reaction on the
run that posted the message, and treats the summon reaction as its reactor tagging
Open SWE. It starts no run of MDA's.
"""

from fastapi import APIRouter, BackgroundTasks, Depends
from managed_deepagents import SlackEndpointRequest

from openswe.config import ENV
from openswe.incidents import channels as incidents
from openswe.mda_channels.auth import require_mda_channel
from openswe.review_guide.launch import close_guide_for_channel
from openswe.slack.channels import DIRECTORY_EVENTS, SlackChannel
from openswe.slack.feedback import (
    FEEDBACK_REACTIONS,
    process_slack_reaction_added,
    process_slack_reaction_removed,
)
from openswe.slack.payloads import SlackEvent
from openswe.slack.summon import SUMMON_REACTIONS, process_slack_summon_reaction
from openswe.webhooks import common

router = APIRouter(prefix="/mda", dependencies=[Depends(require_mda_channel)])


@router.post("/slack/channel-events")
async def slack_channel_event(
    call: SlackEndpointRequest, background_tasks: BackgroundTasks
) -> None:
    """Record a Slack channel's own change; ``None`` starts no run."""
    event = call["event"]
    raw = event["payload"]
    kind = str(raw.get("type") or "")
    channel_id = event.get("channelId", "")
    if not channel_id:
        return None
    if kind in DIRECTORY_EVENTS:
        background_tasks.add_task(SlackChannel.observe, kind, channel_id)
    # Incidents checks the workspace and app as for Slack's own delivery: MDA names
    # the workspace Trigger Server verified, and the app is this deployment's.
    await incidents.handle_slack_event(
        {
            "event": raw,
            "team_id": event.get("teamId", ""),
            "api_app_id": ENV.SLACK_APP_ID.optional() or "",
            "event_id": event["deliveryId"],
        },
        background_tasks,
    )
    if kind == "channel_archive":
        background_tasks.add_task(close_guide_for_channel, channel_id)
    return None


@router.post("/slack/reactions")
async def slack_reaction(call: SlackEndpointRequest, background_tasks: BackgroundTasks) -> None:
    """Act on a reaction as ``/webhooks/slack`` does; ``None`` starts no run of MDA's."""
    event = call["event"]
    raw = dict(event["payload"])
    reaction = event.get("reaction")
    delivery_id = event["deliveryId"]
    if reaction in FEEDBACK_REACTIONS:
        process = (
            process_slack_reaction_removed if event.get("removed") else process_slack_reaction_added
        )
        background_tasks.add_task(process, raw, delivery_id)
    elif reaction in SUMMON_REACTIONS and not event.get("removed"):
        summon = SlackEvent.parse(raw)
        item = summon.item if summon is not None else None
        if summon is None or item is None or not item.channel:
            return None
        channel_context = await common.resolve_slack_channel_context(item.channel, use_cache=False)
        if channel_context.allows_operations:
            background_tasks.add_task(
                process_slack_summon_reaction,
                summon,
                delivery_id,
                channel_context=channel_context,
                bot_user_id=common.SLACK_BOT_USER_ID,
                team_id=str(raw.get("team") or ""),
            )
    return None
