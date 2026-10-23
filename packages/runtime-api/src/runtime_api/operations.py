"""The backend's operations, which the runtime's tools call to do their work.

An operation takes its arguments and returns its result. The tool call around it,
with its result message, errors and transcript, is the runtime's.
"""

from typing import Literal

from pydantic import BaseModel, JsonValue

from runtime_api.endpoint import Endpoint


class RunRequest(BaseModel):
    """Every call names the run it belongs to; the token says whose run it is."""

    run_id: str | None = None


class RunOperation(RunRequest):
    name: str
    arguments: dict[str, JsonValue] = {}
    # Identifies this call, for operations that are idempotent per request.
    request_id: str
    # The run state keys the operation's spec says it reads.
    state: dict[str, JsonValue] = {}
    # The conversation so far, for an operation whose spec says it reads it.
    messages: list[dict[str, JsonValue]] | None = None


class OperationResult(BaseModel):
    # What the model reads, as a tool result's content.
    content: str | list[JsonValue]
    status: Literal["success", "error"] = "success"
    # Data the dashboard renders, as a tool result's artifact.
    artifact: JsonValue = None
    # An id the result message must carry, such as a client tool's pending result.
    message_id: str | None = None
    # Run state the operation changed.
    state: dict[str, JsonValue] = {}
    # What the operation changed in the thread's checkouts, for the turn's git metadata.
    evidence: dict[str, JsonValue] = {}
    # Custom stream events the operation emitted, for the run to emit.
    events: list[JsonValue] = []
    # The thread's box did not answer; the backend has told the person, and the run ends.
    sandbox_unreachable: bool = False


OPERATION = Endpoint("operation", RunOperation, OperationResult)


class ShellCheck(RunRequest):
    """A shell command a tool is about to run in the thread's box."""

    tool: str
    command: str


class ShellVerdict(BaseModel):
    allowed: bool
    # The command to run in its place, such as an approved push with its fix applied.
    command: str | None = None
    # Why it was refused, as the tool result's content.
    refusal: str | None = None


CHECK_SHELL = Endpoint("check_shell", ShellCheck, ShellVerdict)


class ToolEvidence(RunRequest):
    """A tool result of an incident run, recorded as evidence the report can cite."""

    tool: str
    arguments: dict[str, JsonValue] = {}
    content: str | list[JsonValue]
    status: Literal["success", "error"] = "success"
    artifact: JsonValue = None


class EvidenceReply(BaseModel):
    # The result's content, citing the evidence it was recorded as.
    content: str | list[JsonValue]


RECORD_EVIDENCE = Endpoint("record_evidence", ToolEvidence, EvidenceReply)
