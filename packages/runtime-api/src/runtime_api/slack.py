"""The calls MDA's Slack channel makes on the backend, with no run yet.

The backend's ``/webhooks/slack`` gates each Slack message and forwards the ones
MDA's conversations own to Trigger Server, remembering the request. The channel's
handler then asks here what the message starts (``plan_slack_turn``): the backend
builds the run from the request it remembered, as for its own Slack runs, signs
its context, and says whether the message redirects the thread's work in
progress or waits its turn behind it, which the channel starts the run with; or
why it starts nothing, which the handler replies with.
``plan_slack_click`` does the same for a click on the agent's buttons. Every other
click, and one in a conversation the backend holds, goes to ``act_on_slack_click``:
the backend acts on it as it always has.

A run on a conversation MDA's Slack channel holds moves it into a new Slack thread
itself: the backend checks the move (``slack_move``), the run posts the new root
through MDA, which hands the run's thread to it, and the backend records where the
thread went (``slack_moved``). Both act on the run's own thread, from its token.
"""

from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from runtime_api.endpoint import CHANNEL_API_PREFIX, Endpoint
from runtime_api.operations import RunRequest


class PlanSlackTurn(BaseModel):
    """A Slack message the channel received, on its conversation's thread."""

    thread_id: UUID
    # Slack's timestamp of the message, which names it in its channel.
    message_ts: str = Field(min_length=1, max_length=64)
    # The message's Slack address, to find a concierge DM's thread; empty from older deployments.
    channel_id: str = Field(default="", max_length=64)
    thread_ts: str = Field(default="", max_length=64)


class SlackTurnRun(BaseModel):
    input: dict[str, JsonValue]
    context: dict[str, JsonValue]
    metadata: dict[str, JsonValue]
    webhook: str | None = None


class SlackTurnDeclined(BaseModel):
    """Start nothing; the channel's handler tells the Slack thread why."""

    # ``external_channel``: Open SWE doesn't operate in a channel with external
    # participants. ``sign_in``: the person hasn't signed in to Open SWE with
    # GitHub; ``sign_in_again``: their GitHub sign-in is no longer valid.
    reason: Literal["external_channel", "sign_in", "sign_in_again"]
    # Where the person signs in, when the backend has a dashboard URL.
    settings_url: str | None = None


class SlackTurnStart(BaseModel):
    # The run, or none: the message wasn't one the backend handed over, gating
    # answered it already, gating declined it (``declined``), or building the run
    # failed, which the Slack thread heard about.
    run: SlackTurnRun | None = None
    # A mention, a direct message or an explicit request redirects the thread's
    # work in progress; any other follow-up waits for it, as on the backend's own
    # Slack threads.
    interrupts: bool = True
    # A concierge DM's thread, when the run doesn't go on the Slack thread's own.
    thread_id: UUID | None = None
    # Why no run starts, which the handler replies with in the Slack thread; set at
    # most once per webhook ID.
    declined: SlackTurnDeclined | None = None


class OptionClick(BaseModel):
    """One of the agent's option buttons: its label is the person's answer."""

    kind: Literal["option"] = "option"
    text: str = Field(min_length=1, max_length=4000)


class WorkflowPushClick(BaseModel):
    """Approve or reject a push of workflow files the push guard held back."""

    kind: Literal["workflow_push"] = "workflow_push"
    fingerprint: str = Field(min_length=1, max_length=256)
    approved: bool


class RetryClick(BaseModel):
    """Send a failed request again: the message that failed, in its Slack thread."""

    kind: Literal["retry"] = "retry"
    thread_ts: str = Field(min_length=1, max_length=64)
    message_ts: str = Field(default="", max_length=64)
    reply_thread_ts: str = Field(default="", max_length=64)


type SlackClick = Annotated[
    OptionClick | WorkflowPushClick | RetryClick, Field(discriminator="kind")
]


class PlanSlackClick(BaseModel):
    """A click the channel's handler let through, in one of its conversations."""

    thread_id: UUID
    channel_id: str = Field(min_length=1, max_length=64)
    # The Slack thread the clicked message is in, as Trigger Server addressed it.
    thread_ts: str = Field(min_length=1, max_length=64)
    message_ts: str = Field(min_length=1, max_length=64)
    action_ts: str = Field(min_length=1, max_length=64)
    # The person who clicked, as Trigger Server verified them from Slack's request.
    user_id: str = Field(min_length=1, max_length=64)
    # The Slack workspace the app's message is in.
    team_id: str = Field(default="", max_length=64)
    click: SlackClick


class SlackClickStart(SlackTurnStart):
    # False when another click on the message was acted on already, so this one
    # changes nothing and starts nothing.
    acted: bool = True
    # The click's conversation is the backend's: ``act_on_slack_click`` acts on it.
    backend: bool = False
    # A workflow push's status once decided; ``None`` when the backend has no such
    # request any more.
    workflow_push: Literal["approved", "rejected"] | None = None


class ActOnSlackClick(BaseModel):
    """A click the backend acts on: Slack's payload, as Trigger Server forwarded it."""

    # The Slack thread of the clicked message, or of the button that opened a modal.
    channel_id: str = Field(min_length=1, max_length=64)
    thread_ts: str = Field(min_length=1, max_length=64)
    # The person who clicked, as Trigger Server verified them from Slack's request.
    user_id: str = Field(min_length=1, max_length=64)
    payload: dict[str, JsonValue]


class SlackClickActed(BaseModel):
    # False for a call already acted on under the same webhook ID.
    acted: bool = True


PLAN_SLACK_TURN: Final = Endpoint(
    "plan_slack_turn",
    PlanSlackTurn,
    SlackTurnStart,
    # Building a turn reads the Slack thread and fetches its images.
    seconds=120.0,
    # Building the run takes the remembered request, so a call is sent again only
    # where the backend never got it; one sent again under the same webhook ID
    # builds the run again, but gets no decline a call answered already. The
    # backend answers 503 while it can't verify the message's channel.
    prefix=CHANNEL_API_PREFIX,
)

PLAN_SLACK_CLICK: Final = Endpoint(
    "plan_slack_click",
    PlanSlackClick,
    SlackClickStart,
    seconds=120.0,
    # The first call for a clicked message claims it; one sent again under the same
    # webhook ID builds the run again, any other starts nothing.
    prefix=CHANNEL_API_PREFIX,
)

ACT_ON_SLACK_CLICK: Final = Endpoint(
    "act_on_slack_click",
    ActOnSlackClick,
    SlackClickActed,
    seconds=60.0,
    # The backend acts once per webhook ID; one sent again changes nothing.
    prefix=CHANNEL_API_PREFIX,
)


class SlackMoveRequest(RunRequest):
    """A move of the run's own Slack conversation into a new Slack thread."""

    # What the new root says, as the agent's ``slack_move_thread`` writes it; None for a
    # person's bare ``/breakout``, whose root names the thread instead.
    message: str | None = None
    channel_id: str | None = None


class SlackMovePlan(BaseModel):
    # The new root's text; None when ``result`` answers already.
    root_text: str | None = None
    # Whether the run posts the root through MDA, which hands its thread to the new Slack
    # thread: the conversation is one MDA's Slack channel holds. Otherwise the backend's
    # ``slack_move_thread`` moves it, as before.
    via_mda: bool = False
    # Another Slack channel the new root goes into; None for the conversation's own.
    channel_id: str | None = None
    # The answer when the checks stopped the move, or when it finished one an earlier
    # call left half done.
    result: dict[str, JsonValue] | None = None


class SlackMoved(RunRequest):
    # The new root the run posted through MDA.
    thread_ts: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+$")]
    # The Slack channel it posted in, when not the conversation's own.
    channel_id: Annotated[str | None, Field(pattern=r"^[CDG][A-Z0-9]+$")] = None
    # Whether the old Slack thread says where the conversation went, as ``/breakout`` does.
    mark_source: bool = False


class SlackMoveResult(BaseModel):
    # What ``slack_move_thread`` answers: ``success``, and where the thread went.
    result: dict[str, JsonValue]


#: On a run's last input message: a person's bare ``/breakout`` in the conversation, which
#: the run moves before it calls the model. ``True``, or the Slack channel id a
#: ``/breakout #channel`` names.
SLACK_MOVE_MARKER: Final = "open_swe_slack_move"

SLACK_MOVE: Final = Endpoint(
    "slack_move", SlackMoveRequest, SlackMovePlan, seconds=60.0, delivery="idempotent"
)
# Records the move once; the old Slack thread hears of it once.
SLACK_MOVED: Final = Endpoint("slack_moved", SlackMoved, SlackMoveResult, seconds=60.0)
