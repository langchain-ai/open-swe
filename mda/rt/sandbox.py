"""The thread's sandbox as the backend describes it: how MDA creates it, and whose it is.

MDA names, creates, adopts and replaces the box. The backend binds the thread to
the box MDA reports and keeps its proxy config. Kept free of ``managed_deepagents``
so the backend's tests can import this package.
"""

from typing import Literal

from pydantic import BaseModel, JsonValue

# The graph's tool-call middleware the box's calls need: its tool errors as results,
# and its push and pull request guards, which keep the commands they check on the backend.
SandboxCheck = Literal["tool_errors", "push_guards"]


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
    create: SandboxCreate
    # The thread whose box this one works in, a task worker's coordinator; None for its own.
    thread_id: str | None = None
    # The file and shell tools this runtime runs in the box itself, rather than the backend.
    tools: list[str] = []
    checks: list[SandboxCheck] = []
    # Paths the graph's file tools read from the backend, such as skills.
    backend_paths: list[str] = []
