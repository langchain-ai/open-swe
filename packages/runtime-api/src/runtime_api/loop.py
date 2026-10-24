"""The calls the runtime's model loop makes on the backend, phase by phase.

The backend runs the graph's own middleware for each phase: preparation before
the run, the follow-up queue, routing and the system prompt before each model
call, the transcript and usage after it, reply nudges after the model, and the
end-of-run work. Graph state those hooks write that the runtime does not know
comes back in ``update``, and goes back with every later call in ``state``.
"""

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
    # Another provider's model, tried once when a call to ``model`` fails.
    fallback: ModelSpec | None = None
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


class Done(BaseModel):
    """The backend did what it was asked; there is nothing more to say."""


# Preparation the runtime makes again on a restarted run anyway.
PREPARE = Endpoint("prepare", Prepare, PhaseResult, delivery="idempotent")
# Builds the next call, so it is sent again to a backend that never took it.
BEFORE_MODEL = Endpoint(
    "before_model", RunCall, BeforeModel, seconds=LOOP_CALL_SECONDS, delivery="unprocessed"
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
# For the dashboard to show the thread's browser.
BROWSER = Endpoint("browser", BrowserReport, Done, seconds=LOOP_CALL_SECONDS)
