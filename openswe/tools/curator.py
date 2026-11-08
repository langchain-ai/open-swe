"""Tools only a workspace's curator thread has: read memories, decide them, publish a prompt,
and name the triggers that recall memories and guidance in place of their bodies."""

from uuid import UUID

from openswe.audit_logs.tools import audit_tool
from openswe.memories.curator import WorkspaceCurator
from openswe.memories.store import (
    MAX_TRIGGER_GUIDANCE_CHARS,
    MAX_TRIGGER_SUMMARY_CHARS,
    MAX_WORKSPACE_PROMPT_CHARS,
    MEMORIES,
    Decision,
    MemoryTrigger,
    Status,
)
from openswe.tools.access import Policy, access

_CURATOR = Policy(trusted="curator")


@access(_CURATOR)
async def list_memories(
    status: Status = "pending",
) -> dict[str, object]:
    """Implement the `list_memories` tool."""
    memories = await MEMORIES.for_curator(WorkspaceCurator.for_run().slug, status)
    return {"ok": True, "memories": [memory.model_dump(mode="json") for memory in memories]}


@access(_CURATOR)
async def read_workspace_prompt() -> dict[str, object]:
    """Implement the `read_workspace_prompt` tool."""
    return {"ok": True, "prompt": await MEMORIES.workspace_prompt(WorkspaceCurator.for_run().slug)}


@access(_CURATOR)
async def decide_memory(memory_id: str, decision: Decision, note: str) -> dict[str, object]:
    """Implement the `decide_memory` tool."""
    try:
        parsed = UUID(memory_id)
    except ValueError:
        return {"ok": False, "error": "memory_id is not a memory id"}
    if not await MEMORIES.decide(WorkspaceCurator.for_run().slug, parsed, decision, note.strip()):
        return {"ok": False, "error": "No memory of this workspace's with that id may be decided."}
    return {"ok": True, "memory_id": memory_id, "status": decision}


@audit_tool()
@access(_CURATOR)
async def publish_workspace_prompt(prompt: str) -> dict[str, object]:
    """Implement the `publish_workspace_prompt` tool."""
    text = prompt.strip()
    if len(text) > MAX_WORKSPACE_PROMPT_CHARS:
        return {"ok": False, "error": f"prompt exceeds {MAX_WORKSPACE_PROMPT_CHARS} characters"}
    workspace = WorkspaceCurator.for_run().slug
    if text == await MEMORIES.workspace_prompt(workspace):
        return {"ok": True, "published": False, "reason": "unchanged"}
    version = await MEMORIES.publish(workspace, text)
    return {"ok": True, "published": True, "version_id": str(version.id)}


@access(_CURATOR)
async def list_memory_triggers() -> dict[str, object]:
    """Implement the `list_memory_triggers` tool."""
    triggers = await MEMORIES.triggers_for_curator(WorkspaceCurator.for_run().slug)
    return {"ok": True, "triggers": [trigger.model_dump(mode="json") for trigger in triggers]}


@audit_tool()
@access(_CURATOR)
async def set_memory_trigger(path: str, summary: str) -> dict[str, object]:
    """Implement the `set_memory_trigger` tool."""
    try:
        parsed = MemoryTrigger.path_of(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    text = " ".join(summary.split())
    if not text:
        return {"ok": False, "error": "summary is empty"}
    if len(text) > MAX_TRIGGER_SUMMARY_CHARS:
        return {"ok": False, "error": f"summary exceeds {MAX_TRIGGER_SUMMARY_CHARS} characters"}
    await MEMORIES.set_trigger(WorkspaceCurator.for_run().slug, parsed, text)
    return {"ok": True, "path": parsed, "summary": text}


@audit_tool()
@access(_CURATOR)
async def set_memory_trigger_guidance(path: str, guidance: str) -> dict[str, object]:
    """Implement the `set_memory_trigger_guidance` tool."""
    try:
        parsed = MemoryTrigger.path_of(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    text = guidance.strip()
    if len(text) > MAX_TRIGGER_GUIDANCE_CHARS:
        return {"ok": False, "error": f"guidance exceeds {MAX_TRIGGER_GUIDANCE_CHARS} characters"}
    if not await MEMORIES.set_guidance(WorkspaceCurator.for_run().slug, parsed, text):
        return {"ok": False, "error": "This workspace has no trigger at that path."}
    return {"ok": True, "path": parsed, "guidance": text}


@audit_tool()
@access(_CURATOR)
async def delete_memory_trigger(path: str) -> dict[str, object]:
    """Implement the `delete_memory_trigger` tool."""
    try:
        parsed = MemoryTrigger.path_of(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not await MEMORIES.delete_trigger(WorkspaceCurator.for_run().slug, parsed):
        return {"ok": False, "error": "This workspace has no trigger at that path."}
    return {"ok": True, "path": parsed}


@audit_tool()
@access(_CURATOR)
async def link_memory_trigger(path: str, memory_id: str, linked: bool = True) -> dict[str, object]:
    """Implement the `link_memory_trigger` tool."""
    try:
        parsed_path = MemoryTrigger.path_of(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        parsed = UUID(memory_id)
    except ValueError:
        return {"ok": False, "error": "memory_id is not a memory id"}
    workspace = WorkspaceCurator.for_run().slug
    if not await MEMORIES.link_trigger(workspace, parsed_path, parsed, linked=linked):
        return {
            "ok": False,
            "error": "This workspace has no such trigger, or no personal memory with that id "
            "you may decide.",
        }
    return {"ok": True, "path": parsed_path, "memory_id": memory_id, "linked": linked}
