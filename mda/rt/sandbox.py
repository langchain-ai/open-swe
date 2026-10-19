"""The thread's sandbox, as the backend names it and would create it.

MDA adopts the box under that name, or creates it there from these options, and
the file tools, ``runtime.backend`` and the browser all use it. The backend binds
the thread to it and keeps its proxy config, so MDA never touches that.
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
    name: str
    create: SandboxCreate
