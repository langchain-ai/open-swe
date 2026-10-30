"""Tools: keep what a thread learned, for its person or for the workspace's curator."""

from typing import Literal
from uuid import UUID

from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.memories.store import MAX_MEMORY_CHARS, MEMORIES, MemoryRow
from openswe.run_config import RunConfig
from openswe.tools.access import Policy, access, resolve_access, unchanged
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User
from openswe.utils.json_types import as_json_object
from openswe.workspaces.store import WORKSPACES

_PERSONAL = Policy(trusted="private", actor="owner", sole=unchanged)
_WORKSPACE = Policy(trusted="anywhere")


async def _person() -> User | None:
    """The person whose request this run acts on, if a person's."""
    login = await resolve_github_login(as_json_object(get_config()))
    return await User.for_login("github", login) if login else None


async def _workspace_id(cfg: RunConfig) -> UUID | None:
    slug = cfg.workspace_slug
    return await WORKSPACES.id_for_slug(slug) if slug else None


@audit_tool()
async def remember_personal(row: MemoryRow) -> dict[str, object]:
    """Audited apart from ``remember``: a personal memory changes how this person's runs go,
    and a workspace one changes nothing until the curator decides it."""
    return {"ok": True, "memory_id": str(row.id)}


@expose_mcp()
@access(
    _WORKSPACE, per_call=lambda args: _PERSONAL if args.get("scope") == "personal" else _WORKSPACE
)
async def remember(
    text: str, scope: Literal["personal", "workspace"], share_with_workspace: bool = False
) -> dict[str, object]:
    """Implement the `remember` tool."""
    body = text.strip()
    if not body:
        return {"ok": False, "error": "text is empty"}
    if len(body) > MAX_MEMORY_CHARS:
        return {"ok": False, "error": f"text exceeds {MAX_MEMORY_CHARS} characters"}
    cfg = RunConfig.from_runtime()
    person = await _person()
    if scope == "personal" and person is None:
        return {
            "ok": False,
            "error": "Could not tell whose memory this is; personal memories need a person.",
        }
    workspace_id = await _workspace_id(cfg)
    if scope == "workspace" and workspace_id is None:
        return {"ok": False, "error": "This thread has no workspace to remember it for."}
    private = (await resolve_access(cfg)).private
    row = await MEMORIES.save(
        scope=scope,
        body=body,
        person=person.id if person else None,
        workspace_id=workspace_id,
        thread_id=cfg.thread_id,
        from_private_thread=private,
        shared=share_with_workspace,
    )
    if scope == "personal":
        await remember_personal(row)
    return {
        "ok": True,
        "memory_id": str(row.id),
        "status": row.status,
        "applies": (
            "to this person's future runs now; follow it for the rest of this thread too"
            if scope == "personal"
            else "once the workspace's curator adopts it, never sooner"
        ),
        **(
            {"held": "Saved in a private thread and not shared: the curator will never see it."}
            if row.status == "held"
            else {}
        ),
    }


@expose_mcp()
@audit_tool()
@access(_PERSONAL)
async def forget(memory_id: str) -> dict[str, object]:
    """Implement the `forget` tool."""
    person = await _person()
    if person is None:
        return {"ok": False, "error": "Could not tell whose memories to change."}
    try:
        parsed = UUID(memory_id)
    except ValueError:
        return {"ok": False, "error": "memory_id is not a memory id"}
    if not await MEMORIES.forget(parsed, person.id):
        return {"ok": False, "error": "No personal memory of yours has that id."}
    return {"ok": True, "memory_id": memory_id}
