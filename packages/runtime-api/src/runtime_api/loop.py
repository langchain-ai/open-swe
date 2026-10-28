"""The calls the runtime's model loop makes on the backend.

The runtime's middleware runs the loop: it nudges, routes, assembles each call
and ends the run as the catalog's ``LoopRules`` say. It asks the backend only for
what needs the backend's data or credentials, one named call each: the route a
task takes, a model's settings, the task a thread belongs to, a fresh GitHub proxy
token, a reply posted on the model's behalf, and what the end of the run records.
The transcript and usage hear of each reply and tool call.

Preparation, and every phase of a run still answered from its plan, are phase
calls: the backend runs the graph's own middleware for the phase, and graph state
it writes comes back in ``update`` and goes back with every later call in ``state``.

Before each model call the runtime also reads the thread's inbox: what was sent
to the thread while its run worked, and only that. The request names the run and
how its catalog says to read the inbox; the backend keeps what each thread is owed
until a run receives it.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, JsonValue

from runtime_api.catalog import InboxSpec, ModelSpec
from runtime_api.endpoint import LOOP_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest


class RunCall(RunRequest):
    """A call from the loop: the conversation, and the graph state the backend keeps."""

    messages: list[dict[str, JsonValue]] = []
    state: dict[str, JsonValue] = {}
    # The ``task`` calls the calling subagent is nested in; empty for the agent itself.
    namespace: list[str] = []


class Prepare(RunCall):
    # The thread's box, which the runtime opened before preparing; None when it could not.
    sandbox_id: str | None = None
    # Why the runtime could not open it, secrets removed.
    sandbox_error: str | None = None


class PhaseResult(BaseModel):
    """What a phase's hooks wrote: graph state, messages, and where the loop goes next."""

    update: dict[str, JsonValue] = {}
    messages: list[dict[str, JsonValue]] = []
    jump_to: str | None = None
    # Custom stream events the hooks emitted, for the runtime to emit again.
    events: list[JsonValue] = []


class ModelCall(BaseModel):
    """What the graph's own middleware says the next model call sends."""

    system_prompt: str
    # Tools the graph's own middleware takes off this call's list.
    hidden_tools: list[str] = []
    model: ModelSpec | None = None
    # What a call to that model goes to instead when the conversation holds an image:
    # None where the model reads images itself.
    vision: ModelSpec | None = None
    # Another provider's model, tried once when a call to ``model`` fails.
    fallback: ModelSpec | None = None
    use_gateway: bool = False


class BeforeModel(PhaseResult):
    # None when a hook sent the loop elsewhere.
    call: ModelCall | None = None


class AfterAgent(PhaseResult):
    # What the turn's trace records about the run's git work.
    trace_metadata: dict[str, JsonValue] = {}


class Report(RunRequest):
    """What the runtime tells the backend happened: only what the report is about, never
    the conversation or the run's state."""

    # The ``task`` calls the reporting subagent is nested in; empty for the agent itself.
    namespace: list[str] = []
    # The turn's git trace (``git_trace_*``): its baseline and what each call changed.
    git: dict[str, JsonValue] = {}


class ModelResult(Report):
    response: list[dict[str, JsonValue]]
    # The human messages added since the model last answered, which the transcript records.
    humans: list[dict[str, JsonValue]] = []
    # The route the call took, which usage tags the reply with.
    model_route: str | None = None
    # A conversation offload's status, which the transcript notes once.
    offloading: dict[str, JsonValue] | None = None


class ToolCallReport(Report):
    """A tool call the runtime runs, for the transcript to record."""

    name: str
    arguments: dict[str, JsonValue] = {}
    tool_call_id: str
    # The reply that made the call; None for a call code mode made.
    message_id: str | None = None


class ToolResultReport(ToolCallReport):
    # The call's result, as a tool message.
    result: dict[str, JsonValue]


class ToolResultReply(BaseModel):
    # What the call changed in the thread's checkouts, for the turn's git metadata.
    update: dict[str, JsonValue] = {}


class RunEnded(Report):
    # The run's replies without their content: their ids, usage and model, for usage.
    replies: list[dict[str, JsonValue]] = []


class Routed(RunRequest):
    # The run's human messages, the latest of which the route is for.
    humans: list[dict[str, JsonValue]] = []


class ScoutPlanEnd(RunRequest):
    # What the people who asked for the pull request said, as the scout summarized it.
    human_input_summary: str = ""


class SandboxGone(RunRequest):
    # The box a tool found gone, when its error named it.
    sandbox_id: str | None = None


class BrowserReport(RunRequest):
    """The LangSmith browser the runtime opened, or closed, for the thread's agent."""

    browser: str
    opened: bool = True


class InboxItems(BaseModel):
    """What an inbox reply carries: its queued follow-ups, its events and task messages."""

    queued: list[int] = []
    events: list[UUID] = []


class InboxRead(RunRequest):
    # The catalog's ``inbox``; None reads nothing.
    spec: InboxSpec | None = None


class Inbox(BaseModel):
    """What was sent to the thread since the last model call, oldest first."""

    messages: list[dict[str, JsonValue]] = []
    # Graph state taking them changes, such as the surface replies move to.
    update: dict[str, JsonValue] = {}
    # Owed until the run hands them back through ``inbox_received``, once the model
    # call it read them for goes ahead.
    items: InboxItems = InboxItems()


class InboxReceipt(RunRequest):
    items: InboxItems


class Done(BaseModel):
    """The backend did what it was asked; there is nothing more to say."""


class GitHubProxyRefreshed(BaseModel):
    """The box's GitHub proxy was given a fresh token."""

    # When that token expires; None when the thread's box has no GitHub proxy to refresh.
    expires_at: datetime | None = None


class Route(BaseModel):
    # One of the routes ``RoutingRules.route_model_ids`` names, or ``default``.
    route: str


class ChosenModel(RunRequest):
    """A model the run calls by id, such as one a person asked for, at an effort."""

    model_id: str
    effort: str | None = None


class TaskContext(BaseModel):
    """The task the thread belongs to, as each model call tells the model."""

    # Added to each call's system prompt: the thread's role in the task.
    instructions: str = ""
    # The task's context, added to the conversation where the model no longer sees it.
    message: str | None = None


class ReplyOnBehalf(RunCall):
    """The model's final answer, posted for it after it ignored the reply tool."""

    text: str


# Preparation the runtime makes again on a restarted run anyway.
PREPARE = Endpoint("prepare", Prepare, PhaseResult, delivery="idempotent")
# Builds the next call, so it is sent again to a backend that never took it.
BEFORE_MODEL = Endpoint(
    "before_model", RunCall, BeforeModel, seconds=LOOP_CALL_SECONDS, delivery="unprocessed"
)
# Only reads, so it is safe to send again.
INBOX = Endpoint("inbox", InboxRead, Inbox, seconds=LOOP_CALL_SECONDS, delivery="idempotent")
# What a model call will answer is owed no more; receiving it twice is a no-op.
INBOX_RECEIVED = Endpoint(
    "inbox_received", InboxReceipt, Done, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
MODEL_RESULT = Endpoint("model_result", ModelResult, Done, seconds=LOOP_CALL_SECONDS)
AFTER_MODEL = Endpoint("after_model", RunCall, PhaseResult, seconds=LOOP_CALL_SECONDS)
AFTER_AGENT = Endpoint("after_agent", RunCall, AfterAgent, seconds=LOOP_CALL_SECONDS)
TOOL_STARTED = Endpoint("tool_started", ToolCallReport, Done, seconds=LOOP_CALL_SECONDS)
TOOL_RESULT = Endpoint("tool_result", ToolResultReport, ToolResultReply, seconds=LOOP_CALL_SECONDS)
# The runtime replaced the thread's box: the backend connects to the fresh one.
SANDBOX_REPLACED = Endpoint("sandbox_replaced", RunRequest, Done, seconds=LOOP_CALL_SECONDS)
# A tool the runtime ran found the box gone: the backend tells the person.
SANDBOX_UNREACHABLE = Endpoint("sandbox_unreachable", SandboxGone, Done, seconds=LOOP_CALL_SECONDS)
# For the dashboard to show the thread's browser.
BROWSER = Endpoint("browser", BrowserReport, Done, seconds=LOOP_CALL_SECONDS)
# Classifies the task the conversation asks for; only reads, so it is safe to send again.
ROUTE_MODEL = Endpoint(
    "route_model", Routed, Route, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
MODEL_CHOICE = Endpoint(
    "model_choice", ChosenModel, ModelSpec, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
TASK_CONTEXT = Endpoint(
    "task_context", RunRequest, TaskContext, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
# Mints a token and sets it on the box, so sending it again only mints another.
REFRESH_GITHUB_PROXY = Endpoint(
    "refresh_github_proxy",
    RunRequest,
    GitHubProxyRefreshed,
    seconds=LOOP_CALL_SECONDS,
    delivery="idempotent",
)
REPLY_ON_BEHALF = Endpoint("reply_on_behalf", ReplyOnBehalf, Done, seconds=LOOP_CALL_SECONDS)
# The run ended: the transcript closes its turn, usage is finalized and the turn's git
# work reconciled.
RUN_ENDED = Endpoint("run_ended", RunEnded, AfterAgent, seconds=LOOP_CALL_SECONDS)
# The run ended at its model-call limit: the person is told.
STEP_LIMIT_REACHED = Endpoint("step_limit_reached", RunRequest, Done, seconds=LOOP_CALL_SECONDS)
# A review run ended: its check run on the pull request is settled if it was not.
SETTLE_REVIEW_CHECK = Endpoint("settle_review_check", RunRequest, Done, seconds=LOOP_CALL_SECONDS)
# A scout run ended: what it left unplanned goes to Other.
FINISH_SCOUT_PLAN = Endpoint("finish_scout_plan", ScoutPlanEnd, Done, seconds=LOOP_CALL_SECONDS)
