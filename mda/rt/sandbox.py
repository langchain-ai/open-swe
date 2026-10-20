"""The thread's sandbox as the backend describes it: how MDA creates it, and whose it is.

MDA names, creates, adopts and replaces the box. The backend binds the thread to
the box MDA reports and keeps its proxy config. Kept free of ``managed_deepagents``
so the backend's tests can import this package.
"""

from pydantic import BaseModel, JsonValue


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
