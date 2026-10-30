"""The call MDA's Slack channel makes on the backend, with no run yet.

The backend's ``/webhooks/slack`` gates each Slack message and forwards the ones
MDA's conversations own to Trigger Server, remembering the request. The channel's
handler then asks here what the message starts (``plan_slack_turn``): the backend
builds the run from the request it remembered, as for its own Slack runs, signs
its context, and says whether the message redirects the thread's work in
progress or waits its turn behind it, which the channel starts the run with.
"""

from typing import Final
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from runtime_api.endpoint import CHANNEL_API_PREFIX, Endpoint


class PlanSlackTurn(BaseModel):
    """A Slack message the channel received, on its conversation's thread."""

    thread_id: UUID
    # Slack's timestamp of the message, which names it in its channel.
    message_ts: str = Field(min_length=1, max_length=64)


class SlackTurnRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class SlackTurnStart(BaseModel):
    # The run, or none: the message wasn't one the backend handed over, gating
    # answered it already (such as asking the person to connect an account), or
    # building the run failed, which the Slack thread heard about.
    run: SlackTurnRun | None = None
    # A mention, a direct message or an explicit request redirects the thread's
    # work in progress; any other follow-up waits for it, as on the backend's own
    # Slack threads.
    interrupts: bool = True


PLAN_SLACK_TURN: Final = Endpoint(
    "plan_slack_turn",
    PlanSlackTurn,
    SlackTurnStart,
    # Building a turn reads the Slack thread and fetches its images.
    seconds=120.0,
    # Building the run takes the remembered request, so a call is sent again only
    # where the backend never got it; one sent again under the same webhook ID
    # builds the run again.
    prefix=CHANNEL_API_PREFIX,
)
