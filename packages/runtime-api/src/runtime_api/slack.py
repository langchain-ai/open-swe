"""The calls MDA's Slack channel makes on the backend, with no run yet.

The channel's handler decides what a Slack message starts and builds its prompt
from Slack; it asks here only for what needs Open SWE's data. First the facts its
decision needs (``slack_message_facts``): where the message is, who sent it, who
holds its conversation. Then the turn (``start_slack_turn``): the backend gates
it, makes the conversation MDA's, records the thread, signs the run's context, and
answers what the prompt needs from its records; or why it starts nothing, which
the handler replies with. A ``/btw`` question, a breakout and a move are built
whole here, as the backend always built them.
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


class SlackMessage(BaseModel):
    """A Slack message the channel received, on its conversation's thread."""

    thread_id: UUID
    # The app and workspace in Trigger Server's verified key.
    app_id: str = Field(min_length=1, max_length=64)
    team_id: str = Field(min_length=1, max_length=64)
    channel_id: str = Field(min_length=1, max_length=64)
    # Slack's ``channel_type``, such as ``im`` for a direct message.
    channel_type: str = Field(default="", max_length=32)
    # Slack's timestamp of the message, which names it in its channel.
    message_ts: str = Field(min_length=1, max_length=64)
    # The event's own timestamp, when it isn't the message's, as an edit's.
    event_ts: str = Field(default="", max_length=64)
    # The Slack thread's first message; ``message_ts`` when it starts one.
    thread_ts: str = Field(min_length=1, max_length=64)
    # Slack's ``thread_ts``: empty for a message outside any Slack thread.
    reply_thread_ts: str = Field(default="", max_length=64)
    subtype: str = Field(default="", max_length=64)
    # The person who sent it, as Trigger Server verified them; empty for a bot.
    user_id: str = Field(default="", max_length=64)
    # A bot's message: its bot and app.
    bot_id: str = Field(default="", max_length=64)
    bot_app_id: str = Field(default="", max_length=64)
    text: str = Field(default="", max_length=40_000)
    attachments: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=100)
    # Whether it mentions Open SWE, as the channel's key says.
    explicit_mention: bool = False


class SlackMessageFactsRequest(SlackMessage):
    # A ``/workspace:<name>`` tag the message carries, to check it names a workspace.
    workspace_tag: str = Field(default="", max_length=128)


class SlackMessageFacts(BaseModel):
    """What deciding a Slack message needs from Open SWE's records."""

    # ``external``: the channel has participants from outside the workspace;
    # ``unverified``: Slack can't confirm it hasn't. Open SWE operates in neither.
    channel: Literal["ok", "external", "unverified"] = "ok"
    # A direct message with the app.
    dm: bool = False
    code_channel: bool = False
    kitchen_channel: bool = False
    # The sender turned concierge mode on: their DM is one conversation.
    concierge: bool = False
    # A bot's message: whether the bot is on Open SWE's allow-list, and its name there.
    allowed_bot: bool = False
    bot_name: str = ""
    # An untagged reply the agent answers anyway, in a thread only one person talks in.
    solo_followup: bool = False
    # Who holds the Slack thread's conversation: MDA's channel, the backend, or nobody yet.
    conversation: Literal["mda", "backend", "new"] = "new"
    # Whether ``workspace_tag`` names a workspace.
    workspace_known: bool = False


class StartSlackTurn(SlackMessage):
    """A message the handler decided starts a turn, with what it read from Slack."""

    # ``turn``: a message in the conversation, whose prompt the handler builds.
    # ``by_the_way``, ``breakout``, ``web``, ``move`` (a bare ``/breakout``) and
    # ``edit`` (a message the conversation holds, edited by its author) are built here
    # whole.
    kind: Literal["turn", "by_the_way", "breakout", "web", "move", "edit"] = "turn"
    # From ``slack_message_facts``, as the handler decided with them.
    concierge: bool = False
    kitchen_channel: bool = False
    # A DM, a kitchen channel or a solo follow-up: every message counts as a request.
    treat_all_messages_as_mentions: bool = False
    # A ``/model:perf`` command, which redirects the work in progress.
    performance_command: bool = False
    # The request's text with the mention, a known workspace tag and the model command
    # taken out, which titles a new thread.
    clean_text: str = Field(default="", max_length=40_000)
    workspace_tag: str = Field(default="", max_length=128)
    # Whether the prompt carries images, which may need a model that reads them.
    has_images: bool = False
    # The people whose messages the prompt may quote, and who spoke in the Slack thread.
    context_user_ids: list[str] = Field(default_factory=list, max_length=1000)
    participant_user_ids: list[str] = Field(default_factory=list, max_length=1000)
    # The Slack thread holds the agent's review request card.
    review_card: bool = False
    # A bare ``/breakout #channel``: the Slack channel it names.
    move_channel_id: str = Field(default="", max_length=64)


class SlackTurnPrompt(BaseModel):
    """What the handler builds a turn's prompt with, from Open SWE's records."""

    # The thread has had no turn yet.
    first_turn: bool
    # The sender's name, as Slack's profile has it.
    sender_name: str = ""
    # GitHub logins and person ids of the Slack users with an Open SWE account.
    logins: dict[str, str] = Field(default_factory=dict)
    person_ids: dict[str, str] = Field(default_factory=dict)
    # A private thread: the Slack users whose messages the prompt may quote; ``None``
    # when the thread isn't private.
    promptable_user_ids: list[str] | None = None
    # The conversation's channel, as its context block describes it.
    channel: dict[str, JsonValue] = Field(default_factory=dict)
    # Sections from Open SWE's records this turn adds, such as the pull request a
    # review card asks about.
    turn_sections: list[str] = Field(default_factory=list)
    # A review guide's thread: its constant guidance is the guide's own.
    review_guide: bool = False


class SlackTurnRun(BaseModel):
    # Empty for a ``turn``, whose prompt the handler builds.
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
    # The run, or none: gating answered the message already, declined it
    # (``declined``), or building the run failed, which the Slack thread heard about.
    run: SlackTurnRun | None = None
    # A mention, a direct message or an explicit request redirects the thread's
    # work in progress; any other follow-up waits for it, as on the backend's own
    # Slack threads.
    interrupts: bool = True
    # A concierge DM's thread, or a branch's (a `/btw` question's, a breakout's), when
    # the run doesn't go on the Slack thread's own.
    thread_id: UUID | None = None
    # ``thread_id`` is a new branch of the event's conversation: the channel starts it
    # with what the conversation's thread holds, which the run's prompt leaves out.
    branches_conversation: bool = False
    # Why no run starts, which the handler replies with in the Slack thread; set at
    # most once per webhook ID.
    declined: SlackTurnDeclined | None = None


class SlackTurnStarted(SlackTurnStart):
    # A ``turn``'s prompt facts, with its run.
    prompt: SlackTurnPrompt | None = None


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


SLACK_MESSAGE_FACTS: Final = Endpoint(
    "slack_message_facts",
    SlackMessageFactsRequest,
    SlackMessageFacts,
    seconds=60.0,
    # Reads, and notes a mention for later replies, which a call sent again repeats
    # harmlessly. The backend answers 503 while it can't verify the message's channel.
    delivery="idempotent",
    prefix=CHANNEL_API_PREFIX,
)

START_SLACK_TURN: Final = Endpoint(
    "start_slack_turn",
    StartSlackTurn,
    SlackTurnStarted,
    # A breakout opens its Slack thread and reads it.
    seconds=120.0,
    # One sent again under the same webhook ID builds the run again, but gets no
    # decline a call answered already.
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
