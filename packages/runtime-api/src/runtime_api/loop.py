"""The calls the runtime's model loop makes on the backend, phase by phase.

The backend runs the graph's own middleware for each phase: preparation before
the run, routing and the system prompt before each model call, the transcript
and usage after it, reply nudges after the model, and the end-of-run work. Graph
state those hooks write that the runtime does not know comes back in ``update``,
and goes back with every later call in ``state``.

Before each model call the runtime also reads the thread's inbox: what was sent
to the thread while its run worked, and only that. The request names nothing but
the run; the backend keeps what each thread is owed until a run receives it.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, JsonValue

from runtime_api.catalog import ModelSpec
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
    use_gateway: bool = False


class BeforeModel(PhaseResult):
    # None when a hook sent the loop elsewhere.
    call: ModelCall | None = None


class AfterAgent(PhaseResult):
    # What the turn's trace records about the run's git work.
    trace_metadata: dict[str, JsonValue] = {}


class ModelResult(RunCall):
    response: list[dict[str, JsonValue]]


class ObservedResponse(BaseModel):
    """The response as the transcript and usage middleware left it."""

    response: list[dict[str, JsonValue]] = []


class ToolCallReport(RunCall):
    """A tool call the runtime runs, for the transcript to record."""

    name: str
    arguments: dict[str, JsonValue] = {}
    tool_call_id: str


class ToolResultReport(ToolCallReport):
    # The call's result, as a tool message.
    result: dict[str, JsonValue]


class ToolResultReply(BaseModel):
    # What the call changed in the thread's checkouts, for the turn's git metadata.
    update: dict[str, JsonValue] = {}


class SandboxGone(RunCall):
    # The box a tool found gone, when its error named it.
    sandbox_id: str | None = None


class BrowserReport(RunCall):
    """The LangSmith browser the runtime opened, or closed, for the thread's agent."""

    browser: str
    opened: bool = True


type ModelFailureCode = Literal[
    "provider_overloaded",
    "provider_rate_limited",
    "provider_unavailable",
    "provider_timeout",
    "context_too_long",
    "model_unavailable",
]


class ModelFailure(RunRequest):
    """A model call that failed at its provider, classified while the error was intact.

    The run then ends with that error, whose class name is all its end reports; the
    backend keeps this for the reply that tells the person why.
    """

    code: ModelFailureCode
    # The exception's class name, as the run's end reports it.
    error_type: str
    # provider:model, when the call named its model.
    model_id: str | None = None
    # Whether the call went through the LangSmith Gateway, which can fail it too.
    via_gateway: bool = False


class InboxItems(BaseModel):
    """What an inbox reply carries: its queued follow-ups, its events and task messages."""

    queued: list[int] = []
    events: list[UUID] = []


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


# Preparation the runtime makes again on a restarted run anyway.
PREPARE = Endpoint("prepare", Prepare, PhaseResult, delivery="idempotent")
# Builds the next call, so it is sent again to a backend that never took it.
BEFORE_MODEL = Endpoint(
    "before_model", RunCall, BeforeModel, seconds=LOOP_CALL_SECONDS, delivery="unprocessed"
)
# Only reads, so it is safe to send again.
INBOX = Endpoint("inbox", RunRequest, Inbox, seconds=LOOP_CALL_SECONDS, delivery="idempotent")
# What a model call will answer is owed no more; receiving it twice is a no-op.
INBOX_RECEIVED = Endpoint(
    "inbox_received", InboxReceipt, Done, seconds=LOOP_CALL_SECONDS, delivery="idempotent"
)
MODEL_RESULT = Endpoint("model_result", ModelResult, ObservedResponse, seconds=LOOP_CALL_SECONDS)
AFTER_MODEL = Endpoint("after_model", RunCall, PhaseResult, seconds=LOOP_CALL_SECONDS)
AFTER_AGENT = Endpoint("after_agent", RunCall, AfterAgent, seconds=LOOP_CALL_SECONDS)
TOOL_STARTED = Endpoint("tool_started", ToolCallReport, Done, seconds=LOOP_CALL_SECONDS)
TOOL_RESULT = Endpoint("tool_result", ToolResultReport, ToolResultReply, seconds=LOOP_CALL_SECONDS)
# The runtime replaced the thread's box: the backend connects to the fresh one.
SANDBOX_REPLACED = Endpoint("sandbox_replaced", RunCall, Done, seconds=LOOP_CALL_SECONDS)
# A tool the runtime ran found the box gone: the backend tells the person.
SANDBOX_UNREACHABLE = Endpoint("sandbox_unreachable", SandboxGone, Done, seconds=LOOP_CALL_SECONDS)
# A model call failed at its provider: the backend keeps why for the run's failure reply.
MODEL_FAILURE = Endpoint("model_failure", ModelFailure, Done, seconds=LOOP_CALL_SECONDS)
# For the dashboard to show the thread's browser.
BROWSER = Endpoint("browser", BrowserReport, Done, seconds=LOOP_CALL_SECONDS)
