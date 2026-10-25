"""What the backend plans for a run: its operations, subagents, models and sandbox.

The MDA run reads it once, through ``CATALOG``, and builds its agent
from it: a tool for each operation, the subagents it may delegate to, the models
it calls, and the box its file and shell tools run in.
"""

from typing import Literal

from pydantic import BaseModel, JsonValue

from runtime_api.endpoint import LOOP_CALL_SECONDS, Endpoint
from runtime_api.operations import RunRequest

# The runtime's own tool that finds the run's operations by what they do.
SEARCH_TOOLS = "search_tools"


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]


class RunModels(BaseModel):
    """The models an MDA run builds its agent with, and those the backend picks between for a call."""

    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool
    # The models the graph's model routing picks between, by route name.
    routes: dict[str, ModelSpec] = {}
    # What a text-only model's call goes to once the conversation holds an image.
    vision: ModelSpec | None = None


class OperationSpec(BaseModel):
    """One operation the run may call, as the model sees the tool that calls it."""

    name: str
    description: str
    # JSON schema of the arguments.
    parameters: dict[str, JsonValue]
    # Run state keys the operation reads, which the runtime sends with each call;
    # ``messages`` is the conversation so far.
    reads: list[str] = []
    # Whether it reads all of the run state, the conversation included.
    reads_all: bool = False
    # What code mode calls it, as ``tools.<code_name>``; None when code cannot call it.
    code_name: str | None = None


class SubagentSpec(BaseModel):
    name: str
    description: str
    system_prompt: str
    # None: every tool the parent has.
    tools: list[str] | None = None


class SandboxCreate(BaseModel):
    snapshot_id: str | None = None
    fs_capacity_bytes: int
    vcpus: int | None = None
    mem_bytes: int | None = None
    idle_ttl_seconds: int
    delete_after_stop_seconds: int
    # Create-request fields the SDK does not model, such as ``preserve_memory_on_stop``.
    extra_fields: dict[str, JsonValue] = {}


class SandboxSpec(BaseModel):
    """The thread's box, which MDA creates, owns and runs the file and shell tools in."""

    create: SandboxCreate
    # The thread whose box this one works in, a task worker's coordinator; None for its own.
    thread_id: str | None = None


ShellGuard = Literal["shell_commands"]
FileCheck = Literal["image_reads", "agents_md"]


class FilesSpec(BaseModel):
    """Where the run's file and shell tools act, and what is checked around them."""

    # Paths under these prefixes are the backend's (skills, offloaded images): the
    # runtime's file tools reach them through the files API rather than the box.
    backend_paths: list[str] = []
    # Where large results and binary reads are offloaded; the backend serves blobs under it.
    artifacts_root: str = "/"
    # Shell commands the backend checks before they run: its push and pull request guards.
    guards: list[ShellGuard] = []
    # Checks on what ``read_file`` returns.
    checks: list[FileCheck] = []


class InboxSpec(BaseModel):
    """How the run reads its thread's inbox (``runtime_api.loop.INBOX``)."""

    # Event matches and task messages besides queued follow-ups.
    events: bool = False
    # Where the run starts answering: a dashboard follow-up moves a Slack run's answers to the web.
    surface: Literal["slack", "web"] = "web"
    # The model queued images are prepared for; None prepares them for the thread's.
    model_id: str | None = None
    # Whether a vision model takes the call once the conversation holds an image, so
    # images are kept whatever the model reads.
    image_fallback: bool = False


class ReplyRules(BaseModel):
    """The run owes the person an answer through ``tool``, or says it has none with ``no_reply_tool``."""

    tool: str
    no_reply_tool: str
    # Other tools whose successful call answers the person.
    replies: list[str] = []


class RoutingRules(BaseModel):
    """How the graph's model selection picks the route of each call."""

    # None keeps the default route; "fast" never asks the classifier.
    mode: Literal["auto", "fast"] | None = None
    # The model each route stands for, which the dashboard shows.
    route_model_ids: dict[str, str] = {}


class LoopRules(BaseModel):
    """What the graph's hooks around each model call are built from, planned with the run.

    The runtime sends them with each ``before_model`` and ``after_model`` call, so the
    backend answers those without planning the run again.
    """

    # Tools the graph keeps from every model call.
    hidden_tools: list[str] = []
    # How the graph picks each call's route; None: it picks none.
    routing: RoutingRules | None = None
    reply: ReplyRules | None = None
    # A bridged CLI's result tool, which the run must call before it ends.
    cli_result_tool: str | None = None
    # Whether the thread's review walkthrough adds its rules to each call.
    review_guide: bool = False
    # Whether the thread belongs to a task, as its coordinator or a worker.
    task_coordination: bool = False

    @property
    def after_model(self) -> bool:
        """Whether any hook looks at the model's answer."""
        return self.reply is not None or self.cli_result_tool is not None


class Catalog(BaseModel):
    """The operations, subagents and models of the graph this run plays."""

    operations: list[OperationSpec] = []
    subagents: list[SubagentSpec] = []
    models: RunModels | None = None
    trace_metadata: dict[str, JsonValue] = {}
    # Tools the graph keeps from the model, including MDA's own of the same name.
    excluded: list[str] = []
    # Tools the model calls from code mode rather than directly.
    code_only: list[str] = []
    # Tools the model calls both directly and from code mode.
    also_from_code: list[str] = []
    # Tools the model labels each call of, with what the label argument tells it.
    described: dict[str, str] = {}
    # The thread's box; None runs the file and shell tools through the files API alone.
    sandbox: SandboxSpec | None = None
    files: FilesSpec = FilesSpec()
    # Whether each tool result is recorded as incident evidence, which the result then cites.
    incident_evidence: bool = False
    # Where the Agent Server reports a launched worker's run ending; None launches none.
    worker_webhook: str | None = None
    # The most model calls one run of the graph makes; None leaves it to the runtime.
    model_call_limit: int | None = None
    # Where the run's skills are (``runtime_api.skills``), a later source's skill
    # taking the place of an earlier one's of the same name.
    skill_sources: list[str] = []
    # How the run reads what is sent to its thread while it works; None reads nothing.
    inbox: InboxSpec | None = None
    # What the backend builds the hooks around each model call from.
    loop: LoopRules | None = None
    # Whether the run offers ``search_tools`` over its operations.
    search_tools: bool = False


# Only reads the plan, so it is safe to send again.
CATALOG = Endpoint("catalog", RunRequest, Catalog, seconds=LOOP_CALL_SECONDS, delivery="idempotent")
