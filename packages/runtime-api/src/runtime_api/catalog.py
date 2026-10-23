"""What the backend plans for a run: its operations, subagents, models and sandbox.

The MDA run reads it once, through the ``catalog`` hook, and builds its agent
from it: a tool for each operation, the subagents it may delegate to, the models
it calls, and the box its file and shell tools run in.
"""

from typing import Literal

from pydantic import BaseModel, JsonValue


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]


class RunModels(BaseModel):
    """The models an MDA run builds its agent with."""

    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool


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
