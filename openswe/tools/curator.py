"""Tools only a workspace's curator thread has: read engrams, decide them, publish a prompt."""

from typing import Literal
from uuid import UUID

from openswe.audit_logs.tools import audit_tool
from openswe.engrams.curator import WorkspaceCurator
from openswe.engrams.store import ENGRAMS, MAX_WORKSPACE_PROMPT_CHARS
from openswe.tools.access import Policy, access

_CURATOR = Policy(trusted="curator")


@access(_CURATOR)
async def list_engrams(
    status: Literal["pending", "held", "adopted", "declined"] = "pending",
) -> dict[str, object]:
    """Implement the `list_engrams` tool."""
    engrams = await ENGRAMS.for_curator(WorkspaceCurator.for_run().slug, status)
    return {"ok": True, "engrams": [engram.model_dump(mode="json") for engram in engrams]}


@access(_CURATOR)
async def read_workspace_prompt() -> dict[str, object]:
    """Implement the `read_workspace_prompt` tool."""
    return {"ok": True, "prompt": await ENGRAMS.workspace_prompt(WorkspaceCurator.for_run().slug)}


@access(_CURATOR)
async def decide_engram(
    engram_id: str, decision: Literal["adopted", "declined"], note: str
) -> dict[str, object]:
    """Implement the `decide_engram` tool."""
    try:
        parsed = UUID(engram_id)
    except ValueError:
        return {"ok": False, "error": "engram_id is not an engram id"}
    if not await ENGRAMS.decide(WorkspaceCurator.for_run().slug, parsed, decision, note.strip()):
        return {"ok": False, "error": "No engram of this workspace's with that id may be decided."}
    return {"ok": True, "engram_id": engram_id, "status": decision}


@audit_tool()
@access(_CURATOR)
async def publish_workspace_prompt(prompt: str) -> dict[str, object]:
    """Implement the `publish_workspace_prompt` tool."""
    text = prompt.strip()
    if len(text) > MAX_WORKSPACE_PROMPT_CHARS:
        return {"ok": False, "error": f"prompt exceeds {MAX_WORKSPACE_PROMPT_CHARS} characters"}
    workspace = WorkspaceCurator.for_run().slug
    if text == await ENGRAMS.workspace_prompt(workspace):
        return {"ok": True, "published": False, "reason": "unchanged"}
    version = await ENGRAMS.publish(workspace, text)
    return {"ok": True, "published": True, "version_id": str(version.id)}
