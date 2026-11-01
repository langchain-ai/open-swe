"""What the backend works out for a run as it starts it: its operations, subagents,
models, sandbox and the rules of its middleware.

The run's context always carries it, and the MDA run builds its agent from it: a tool
for each operation and for each tool of the MCP servers it connects to itself, the
subagents it may delegate to, the models it calls, the sandbox its file and shell tools
run in, and what its middleware does around each model call.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, JsonValue, field_validator

from runtime_api.connections import ConnectionRef

# The runtime's own tool that finds the run's operations by what they do.
SEARCH_TOOLS = "search_tools"
# Marks the placeholder result of a call the client runs itself, until it answers.
CLIENT_CALL_PENDING = "open_swe_client_call"


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]
    # Whether the model reads images; a call to one that does not goes to the vision model.
    reads_images: bool = True
    # Another provider's model, tried once when a call to this one fails.
    fallback: ModelSpec | None = None


class RunModels(BaseModel):
    """The models an MDA run builds its agent with, and those the backend picks between for a call."""

    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool
    # The models the graph's model routing picks between, by route name.
    routes: dict[str, ModelSpec] = {}
    # What a text-only model's call goes to once the conversation holds an image.
    vision: ModelSpec | None = None


class ToolSpec(BaseModel):
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
    # The backend's grant of this operation to this run, sent with each call of it.
    grant: str | None = None
    # How long a call of it may take, when shorter than a tool call's allowance.
    seconds: float | None = None
    # The connections its tool reads its credentials from, which the runtime reads as
    # the run and sends with each call of it (``RunOperation.credentials``).
    connections: list[ConnectionRef] = []


class McpServerSpec(BaseModel):
    """An MCP server whose credential is an MDA connection, which the run connects to itself.

    The backend serves every other MCP server's tools as operations; it holds no
    credential for this one.
    """

    # The Open SWE connection's name.
    name: str
    url: str
    transport: Literal["streamable_http", "sse"]
    # The tools the run may call, by their name on the server, each with the name the
    # model calls it by.
    tools: dict[str, str]
    connection: ConnectionRef
    # The header the credential goes in as it is; None sends ``Authorization: Bearer``.
    header: str | None = None
    # What each of its tools' descriptions ends with: where the model's tool comes from.
    provenance: str = ""


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
    """The thread's sandbox, which MDA creates, owns and runs the file and shell tools in."""

    create: SandboxCreate
    sandbox_id: str | None = None
    # The thread whose sandbox this one works in, a task worker's coordinator; None for its own.
    thread_id: str | None = None
    # The connection the sandbox's GitHub rules carry, where MDA writes its proxy config
    # (``runtime_api.sandbox_proxy``); None leaves the proxy config to the backend.
    github_proxy: ConnectionRef | None = None

    @field_validator("github_proxy")
    @classmethod
    def _deployments_own(cls, github: ConnectionRef | None) -> ConnectionRef | None:
        # Everyone who runs on the thread, and its task workers, works in the sandbox, and
        # its proxy config outlives the run: one person's token there reaches them all.
        if github is not None and github.owner != "agent":
            raise ValueError("A sandbox's GitHub proxy reads the deployment's own connection")
        return github


ShellGuard = Literal["shell_commands"]
FileCheck = Literal["image_reads", "agents_md"]


class FilesSpec(BaseModel):
    """Where the run's file and shell tools act, and what is checked around them."""

    # Paths under these prefixes are the backend's (skills, offloaded images): the
    # runtime's file tools reach them through the files API rather than the sandbox.
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
    """The run owes the person on Slack an answer through ``tool``, or says it has none
    with ``no_reply_tool``. An answer without either is asked for again, ``nudge`` up to
    ``max_nudges`` times, then posted on the model's behalf."""

    tool: str
    no_reply_tool: str
    # Where each turn starts answering: Slack for a run asked from there, else the web.
    surface: Literal["slack", "web"] = "web"
    # Other tools whose successful call answers the person.
    replies: list[str] = []
    # The messages that ask again, in the run's input envelope, sent as ``nudge_sender``.
    nudge: list[str] = []
    nudge_sender: str = ""
    max_nudges: int = 2


class CliResultRules(BaseModel):
    """A bridged CLI waits on the run's ``tool`` call; a run that ends without one is asked
    again, ``nudge`` up to ``max_nudges`` times."""

    tool: str
    nudge: list[str] = []
    max_nudges: int = 2


class RoutingRules(BaseModel):
    """How the run picks the route of each model call."""

    # None keeps the default route; "fast" never asks the classifier.
    mode: Literal["auto", "fast"] | None = None
    # The model each route stands for, which the dashboard shows.
    route_model_ids: dict[str, str] = {}
    # Whether a call with no route kept asks the classifier; False takes the default route.
    classify: bool = True


class LoopRules(BaseModel):
    """What the runtime's middleware does around each of the run's model calls."""

    # Opens every call's system prompt where the deployment is not production.
    deployment_prompt: str = ""
    # Tools kept from every model call.
    hidden_tools: list[str] = []
    # Tools kept from a call while the named run state is not set.
    hidden_unless: dict[str, list[str]] = {}
    # Run state that, once set, ends the run before its next model call.
    stop_when: list[str] = []
    # How the run picks each call's route; None: it picks none.
    routing: RoutingRules | None = None
    reply: ReplyRules | None = None
    cli_result: CliResultRules | None = None
    # Whether the thread may belong to a task, as its coordinator or a worker.
    task_coordination: bool = False
    # Whether the run may wait on a call its client runs itself.
    client_tools: bool = False
    # Whether the backend gives the sandbox's GitHub proxy a fresh token before each model call.
    refresh_github_proxy: bool = False
    # What the run's end does besides recording it.
    at_end: list[Literal["settle_review_check", "finish_scout_plan"]] = []


class Catalog(BaseModel):
    """The operations, subagents and models of the graph this run plays."""

    # The run's tools: all but its integrations, which stay on the backend, found with
    # ``search_tools`` and called from code mode (``runtime_api.tool_search``).
    operations: list[ToolSpec] = []
    # The same tools by name alone, as the run's context carries them (``names_only``):
    # the run reads their specs as it starts (``runtime_api.tool_search.TOOL_SPECS``).
    tool_names: list[str] = []
    # MCP servers the run connects to itself; ``search_tools`` finds only the backend's
    # tools, so theirs are offered to each model call.
    mcp_servers: list[McpServerSpec] = []
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
    # The thread's sandbox; None runs the file and shell tools through the files API alone.
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
    # What the runtime's middleware does around each model call; None leaves it to the
    # backend's phase calls, for the runs still answered from their plan.
    loop: LoopRules | None = None
    # Whether the run offers ``search_tools`` over all its tools and calls those its
    # catalog leaves out from code, and how the model reads the search.
    search_tools: bool = False
    search_tools_description: str | None = None
    # The only tools the run may call; the rest stay offered, as the provider's prompt
    # cache needs, and a call of one is refused. None lets every tool run.
    runnable_tools: list[str] | None = None

    def request_prefix(self, tools_digest: str) -> str:
        """A digest of what this catalog puts ahead of a conversation in each model call.

        Two runs with the same one send the same tools, models and loop prompts, so one
        reads from the provider's cache what the other wrote. ``tools_digest`` names the
        specs ``names_only`` leaves out. What only decides how calls run is left out.
        """
        loop = (
            None
            if self.loop is None
            else self.loop.model_dump(
                mode="json", exclude={"reply": True, "cli_result": True, "routing": {"classify"}}
            )
        )
        shape = self.names_only().model_dump(
            mode="json",
            exclude={
                "sandbox",
                "files",
                "inbox",
                "loop",
                "trace_metadata",
                "model_call_limit",
                "runnable_tools",
            },
        )
        if isinstance(models := shape.get("models"), dict):
            # Offered on a thread's first run only; a call reaches it only once the
            # conversation holds an image.
            models.pop("vision", None)
        payload = {"catalog": shape, "loop": loop, "tools": tools_digest}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def names_only(self) -> Catalog:
        """This catalog as a run's context carries it: its tools by name alone.

        The Agent Server copies the context into every checkpoint and every read of
        the run, so the specs stay with the backend, which the run reads them from.
        """
        return self.model_copy(
            update={"operations": [], "tool_names": [tool.name for tool in self.operations]}
        )

    @property
    def needs_tool_specs(self) -> bool:
        """Whether it names tools whose specs the run has yet to read."""
        return bool(self.tool_names) and not self.operations
