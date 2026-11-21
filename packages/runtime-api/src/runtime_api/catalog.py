"""What the backend works out for a run as it starts it: its operations, subagents,
models, sandbox and the rules of its middleware.

The run's context always carries it, and the MDA run builds its agent from it: a tool
for each operation and for each tool of the MCP servers it connects to itself, the
subagents it may delegate to, the models it calls, the sandbox its file and shell tools
run in, and what its middleware does around each model call.
"""

from collections.abc import Mapping
from typing import ClassVar, Final, Literal, Self

from pydantic import BaseModel, JsonValue, model_validator

from runtime_api.connections import ConnectionRef
from runtime_api.provenance import Provenance
from runtime_api.sandbox_setup import GitIdentity

# The runtime's own tool that finds the run's operations by what they do.
SEARCH_TOOLS = "search_tools"
# Marks the placeholder result of a call the client runs itself, until it answers.
CLIENT_CALL_PENDING = "open_swe_client_call"


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]
    # Whether the model reads images; a call to one that does not goes to the vision model.
    reads_images: bool = True
    # Another provider's model, alternated with this one while a call fails transiently.
    fallback: ModelSpec | None = None


class RunModels(BaseModel):
    """The models an MDA run builds its agent with, and those the backend picks between for a call."""

    model: ModelSpec
    subagent_model: ModelSpec

    # The models the graph's model routing picks between, by route name.
    routes: dict[str, ModelSpec] = {}
    # What a text-only model's call goes to once the conversation holds an image.
    vision: ModelSpec | None = None
    # The deployment's fallback model (``LLM_FALLBACK_MODEL_ID``) for a model the runtime
    # picks itself, such as one a person asked for; None falls back to the other provider.
    fallback_model_id: str | None = None


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
    # Whether the runtime runs the tool itself, as this spec describes it, rather than
    # calling the operation; a runtime that cannot run it calls the operation.
    runtime_runs: bool = False


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
    # The other ``create_sandbox`` arguments, from ``EXTRA_FIELDS``.
    extra_fields: dict[str, JsonValue] = {}
    # The environment of every command in the sandbox, set in its own run config. None of it
    # is a credential.
    env_vars: dict[str, str] = {}

    #: The LangSmith SDK ``create_sandbox`` arguments ``extra_fields`` may hold: the ones Robin
    #: handles beyond the snapshot, VM size and TTLs above.
    EXTRA_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "access_delegation",
            "delete_after_stop_seconds",
            "idle_ttl_seconds",
            "mount_config",
            "preserve_memory_on_stop",
            "proxy_config",
            "run_config",
            "tag_value_ids",
        }
    )

    def create_arguments(self) -> dict[str, JsonValue]:
        """``extra_fields`` that go to ``create_sandbox``: only ``EXTRA_FIELDS``, never null.

        Only a record saved before ``EXTRA_FIELDS`` was enforced holds any other key.
        """
        arguments: dict[str, JsonValue] = {
            key: value
            for key, value in self.extra_fields.items()
            if key in self.EXTRA_FIELDS and value is not None
        }
        if "mount_config" in arguments:
            arguments["mount_config"] = self.mount_config_argument(arguments["mount_config"])
        return arguments

    @staticmethod
    def mount_config_argument(mount_config: JsonValue) -> JsonValue:
        """``mount_config`` without its JSON nulls in ``auth``; anything else goes as it is.

        The sandbox API reads a null as absent, so a proxy rule can authenticate the mount
        instead, but the SDK's ``create_sandbox`` counts a null provider as set and rejects
        it next to that provider's proxy rule (and a null ``auth`` outright).
        """
        if not isinstance(mount_config, dict) or "auth" not in mount_config:
            return mount_config
        auth = mount_config["auth"]
        if auth is None:
            without_auth: dict[str, JsonValue] = {
                key: value for key, value in mount_config.items() if key != "auth"
            }
            return without_auth
        if not isinstance(auth, dict):
            return mount_config
        providers: dict[str, JsonValue] = {
            provider: value for provider, value in auth.items() if value is not None
        }
        with_providers: dict[str, JsonValue] = {**mount_config, "auth": providers}
        return with_providers


class SandboxSpec(BaseModel):
    """The thread's sandbox, which MDA creates, owns and runs the file and shell tools in."""

    create: SandboxCreate
    sandbox_id: str | None = None
    # The sandbox's whole proxy config, which MDA creates it with and writes before its
    # commands. It is the same on every run, so it holds no credential: commands get
    # GitHub's and the tool channel's from the backend's proxy callback, which LangSmith
    # asks as they go out. None leaves the sandbox's own alone, as an external one keeps
    # its harness's.
    proxy_config: dict[str, JsonValue] | None = None
    # Deprecated: MDA deployments from before ``proxy_config`` read this and write the
    # proxy config the backend hands over (``runtime_api.sandbox_proxy``); remove once
    # every deployment reads ``proxy_config``.
    writes_proxy: bool = False
    # Whether the run may prepare before its sandbox is open and report the sandbox afterwards
    # (``runtime_api.loop.BIND_SANDBOX``), so its first model call waits for neither: nothing
    # preparing this run does reaches the sandbox. A backend from before that call says nothing.
    bind_after_prepare: bool = False
    # Who commits in the sandbox: the thread's owner, written before the run's first command.
    git_identity: GitIdentity = GitIdentity()
    # Externally created LangSmith sandbox (evals): adopt as-is without setup, proxy writes, or replacement.
    external: bool = False


ShellGuard = Literal["shell_commands"]
FileCheck = Literal["image_reads", "agents_md"]


class FilesSpec(BaseModel):
    """Where the run's file and shell tools act, and what is checked around them."""

    # Paths under these prefixes are the backend's (skills, offloaded images): the
    # runtime's file tools reach them through the files API rather than the sandbox.
    backend_paths: list[str] = []
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


class Nudge(BaseModel):
    """A message that asks the model again, as the run's input renders it, and what it is."""

    text: str
    provenance: Provenance

    @classmethod
    def of(cls, message: Mapping[str, object]) -> Self:
        """The nudge a built input message makes."""
        provenance = Provenance.of(message)
        content = message.get("content")
        if provenance is None or not isinstance(content, str):
            raise ValueError("A nudge is a built input message with text content")
        return cls(text=content, provenance=provenance)


class Nudging(BaseModel):
    """Rules that ask the model again with ``nudges``."""

    nudges: list[Nudge] = []
    # ``nudges``' texts alone, for an MDA deployed before nudges carried their provenance.
    nudge: list[str] = []

    @property
    def asked(self) -> list[tuple[str, Provenance | None]]:
        """Each nudge's text and, when a backend that records it sent it, its provenance."""
        if self.nudges:
            return [(nudge.text, nudge.provenance) for nudge in self.nudges]
        return [(text, None) for text in self.nudge]

    @model_validator(mode="after")
    def _texts_for_older_runtimes(self) -> Self:
        if not self.nudge:
            self.nudge = [nudge.text for nudge in self.nudges]
        return self


class ReplyRules(Nudging):
    """The run owes the person on Slack an answer through ``tool``, or says it has none
    with ``no_reply_tool``. An answer without either is asked for again, ``nudge`` up to
    ``max_nudges`` times, then posted on the model's behalf."""

    tool: str
    no_reply_tool: str
    # Where each turn starts answering: Slack for a run asked from there, else the web.
    surface: Literal["slack", "web"] = "web"
    # Other tools whose successful call answers the person.
    replies: list[str] = []
    # Whom ``nudges`` are sent as.
    nudge_sender: str = ""
    max_nudges: int = 2


# A route a run's model calls take: the classifier picks one of ``ROUTES``; ``default``
# is the run's own model.
RouteName = Literal["fast", "balanced", "performance", "default"]
ROUTES: Final[tuple[RouteName, ...]] = ("fast", "balanced", "performance")


def kept_route(value: object) -> RouteName | None:
    """The route a thread's kept ``model_route`` stands for; None when it names none.

    Threads from before kept ``fast_alt`` for ``fast``.
    """
    match value:
        case "fast" | "fast_alt":
            return "fast"
        case "balanced":
            return "balanced"
        case "performance":
            return "performance"
        case "default":
            return "default"
    return None


class RoutingRules(BaseModel):
    """How the run picks the route of each model call."""

    # None keeps the default route; "fast" never asks the classifier.
    mode: Literal["auto", "fast"] | None = None
    # The classifier still reads the thread's first request so its trace records the task
    # type, but nothing is routed by it.
    shadow: bool = False
    # The model each route stands for, which the dashboard shows, and the effort it runs at.
    route_model_ids: dict[str, str] = {}
    route_efforts: dict[str, str | None] = {}


# Held state: the context of a task the thread joined during the run, after its catalog
# was planned without it (a coordinator's first worker, a baby-sit watch). The next
# run's catalog carries it in ``LoopRules.task_context``.
JOINED_TASK_CONTEXT: Final = "joined_task_context"


class TaskContext(BaseModel):
    """The task the thread belongs to, as each model call tells the model."""

    # The thread's role in the task, added to the conversation when it differs from the
    # role the model last saw there.
    instructions: str | None = None
    # The task's context, added to the conversation where the model no longer sees it.
    message: str | None = None


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
    # The task the thread may belong to, as its coordinator or a worker, as the run
    # started; None where it may not. A coordinator's first worker hands back the context
    # of the task it then joined (``runtime_api.workers.Worker``).
    task_context: TaskContext | None = None
    # Deprecated: what an MDA deployment from before ``task_context`` reads to ask the
    # backend's ``task_context`` before each call. Remove once every deployment reads
    # ``task_context``.
    task_coordination: bool = False
    # Whether the run may wait on a call its client runs itself.
    client_tools: bool = False
    # Open SWE's own framing of the tools the client runs, after every call's system prompt;
    # never words the client wrote, which reach the model only in the conversation.
    client_prompt: str = ""
    # Whether the run was asked only to offload the conversation: it summarizes it and ends.
    offload_conversation: bool = False
    # What the run's end does besides recording it.
    at_end: list[Literal["settle_review_check", "finish_scout_plan"]] = []
    # The model experiment's tier the run's model stands for; None outside the experiment.
    model_experiment: Literal["balanced", "performance"] | None = None

    @property
    def routing_arm(self) -> Literal["active", "inactive"]:
        """Whether the classifier routes the run's model calls."""
        return "active" if self.routing is not None and self.routing.mode == "auto" else "inactive"

    @property
    def trace_tags(self) -> list[str]:
        """The tags the run's trace carries: whether the classifier routes its model calls,
        and the model experiment's tier the run is on."""
        tags = [f"model_routing:{self.routing_arm}"]
        if self.model_experiment is not None:
            tags.append(f"model_experiment:{self.model_experiment}")
        return tags


class BrowserSpec(BaseModel):
    """A review's browser, which opens pages the pull request's author controls."""

    # The domains, each with its subdomains, it may install a sign-in session for.
    session_domains: list[str] = []


class MemoryScope(BaseModel):
    """Where a run was asked from, which decides what memory it may use (``rt.memory_policy``)."""

    # ``public``: a public Slack channel inside the company.
    # ``other``: anywhere else inside the company, like a DM, private channel or group DM.
    # ``external``: anywhere people outside the company may see, or that couldn't be confirmed.
    thread: Literal["public", "other", "external"] = "other"

    @property
    def internal(self) -> bool:
        """Whether only people in the company can see the run's conversation."""
        return self.thread != "external"


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
    trace_tags: list[str] = []
    # Tools the graph keeps from the model, including MDA's own of the same name.
    excluded: list[str] = []
    # Tools the model calls from code mode rather than directly.
    code_only: list[str] = []
    # Tools the model calls both directly and from code mode.
    also_from_code: list[str] = []
    # Tools offered to the model that this run refuses when called, so the tools offered
    # stay the same from run to run and the provider's prompt cache still holds.
    disabled: list[str] = []
    # Tools the model labels each call of, with what the label argument tells it.
    described: dict[str, str] = {}
    # The thread's sandbox, which every run has; None only from a backend from before that,
    # whose run MDA refuses.
    sandbox: SandboxSpec | None = None
    files: FilesSpec = FilesSpec()
    # A review's browser; None gives a review none. The coding agent always has its own.
    browser: BrowserSpec | None = None
    # Where the Agent Server reports a launched worker's run ending; None launches none.
    worker_webhook: str | None = None
    # The most model calls one run of the graph makes; None leaves it to the runtime.
    model_call_limit: int | None = None
    # Where the run's skills are (``runtime_api.skills``), a later source's skill
    # taking the place of an earlier one's of the same name.
    skill_sources: list[str] = []
    # How the run reads what is sent to its thread while it works; None reads nothing.
    inbox: InboxSpec | None = None
    # What the runtime's middleware does around each model call.
    loop: LoopRules = LoopRules()
    # Whether the run offers ``search_tools`` over all its tools and calls those its
    # catalog leaves out from code, and how the model reads the search.
    search_tools: bool = False
    search_tools_description: str | None = None
    # Where the run was asked from, for its memory; None uses no memory.
    memory: MemoryScope | None = None
    # The digest the backend keeps every tool the run may call by, which the run reads
    # once per process to search and resolve them itself (``runtime_api.tool_search``);
    # None searches on the backend.
    tool_set: str | None = None

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
