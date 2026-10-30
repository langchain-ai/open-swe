"""Tools only a workspace's curator thread has: read memories, decide them, publish a prompt."""

from typing import Literal
from uuid import UUID

from openswe.audit_logs.tools import audit_tool
from openswe.memories.store import MAX_WORKSPACE_PROMPT_CHARS, MEMORIES
from openswe.run_config import RunConfig
from openswe.tools.access import Policy, access
from openswe.workspaces.store import WORKSPACES

_CURATOR = Policy(trusted="curator")


async def _workspace_id() -> UUID:
    slug = RunConfig.from_runtime().workspace_slug
    workspace_id = await WORKSPACES.id_for_slug(slug) if slug else None
    if workspace_id is None:
        raise ValueError("This curator's workspace no longer exists")
    return workspace_id


@access(_CURATOR)
async def list_memories(
    status: Literal["pending", "held", "adopted", "declined"] = "pending",
) -> dict[str, object]:
    """Implement the `list_memories` tool."""
    memories = await MEMORIES.for_curator(await _workspace_id(), status)
    return {"ok": True, "memories": [memory.model_dump(mode="json") for memory in memories]}


@access(_CURATOR)
async def read_workspace_prompt() -> dict[str, object]:
    """Implement the `read_workspace_prompt` tool."""
    return {"ok": True, "prompt": await MEMORIES.workspace_prompt(await _workspace_id())}


@access(_CURATOR)
async def decide_memory(
    memory_id: str, decision: Literal["adopted", "declined"], note: str
) -> dict[str, object]:
    """Implement the `decide_memory` tool."""
    try:
        parsed = UUID(memory_id)
    except ValueError:
        return {"ok": False, "error": "memory_id is not a memory id"}
    if not await MEMORIES.decide(await _workspace_id(), parsed, decision, note.strip()):
        return {"ok": False, "error": "No memory of this workspace's with that id may be decided."}
    return {"ok": True, "memory_id": memory_id, "status": decision}


@audit_tool()
@access(_CURATOR)
async def publish_workspace_prompt(prompt: str) -> dict[str, object]:
    """Implement the `publish_workspace_prompt` tool."""
    text = prompt.strip()
    if len(text) > MAX_WORKSPACE_PROMPT_CHARS:
        return {"ok": False, "error": f"prompt exceeds {MAX_WORKSPACE_PROMPT_CHARS} characters"}
    workspace_id = await _workspace_id()
    if text == await MEMORIES.workspace_prompt(workspace_id):
        return {"ok": True, "published": False, "reason": "unchanged"}
    version = await MEMORIES.publish(workspace_id, text)
    return {"ok": True, "published": True, "version_id": str(version.id)}
