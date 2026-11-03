"""A Slack channel's own changes, which MDA's Slack channel hands the backend.

Where Slack's Events URL points at Trigger Server, the backend no longer hears a
channel's creation, rename, archive, unarchive or deletion. MDA's Slack channel
takes them with its ``channel`` handler (``mda/channels/slack.py``) and POSTs them
here, signed with ``MDA_SERVICE_SECRET``, and the backend keeps the same records
``/webhooks/slack`` keeps for them: the channel directory, incident channels and
review guides. It starts no run.
"""

from fastapi import APIRouter, BackgroundTasks, Depends
from managed_deepagents import SlackEndpointRequest

from openswe.config import ENV
from openswe.incidents import channels as incidents
from openswe.mda_channels.auth import require_mda_channel
from openswe.review_guide.launch import close_guide_for_channel
from openswe.slack.channels import DIRECTORY_EVENTS, SlackChannel

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
