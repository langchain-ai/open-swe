"""Tools: keep a memory for the person a thread works for, and offer it to the curator."""

from uuid import UUID

from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.memories.curator import WorkspaceCurator
from openswe.memories.store import MAX_MEMORY_CHARS, MEMORIES, UnknownWorkspaceError
from openswe.run_config import RunConfig
from openswe.tools.access import Policy, access, resolve_access, unchanged
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User
from openswe.utils.json_types import as_json_object

# Where only its person writes, a memory may apply to them at once; anywhere else
# someone else's words could reach it, so it waits for the curator instead.
_PERSONAL = Policy(trusted="private", actor="owner", sole=unchanged)
_ANYWHERE = Policy(trusted="anywhere")


@expose_mcp()
@audit_tool()
@access(_ANYWHERE)
async def remember(text: str) -> dict[str, object]:
    """Implement the `remember` tool."""
    body = text.strip()
    if not body:
        return {"ok": False, "error": "text is empty"}
    if len(body) > MAX_MEMORY_CHARS:
        return {"ok": False, "error": f"text exceeds {MAX_MEMORY_CHARS} characters"}
    cfg = RunConfig.from_runtime()
    login = await resolve_github_login(as_json_object(get_config()))
    person = await User.for_login("github", login) if login else None
    run_access = await resolve_access(cfg)
    personal = person is not None and run_access.mode(_PERSONAL) is not None
    try:
        row = await MEMORIES.save(
            scope="personal" if personal else "workspace",
            body=body,
            person=person.id if person else None,
            workspace=cfg.workspace_slug,
            thread_id=cfg.thread_id,
            from_private_thread=run_access.private,
        )
    except UnknownWorkspaceError:
        return {"ok": False, "error": "This thread has no workspace to remember it for."}
    if row.status == "pending" and cfg.workspace_slug and row.workspace_id is not None:
        await WorkspaceCurator(cfg.workspace_slug).wake_if_rested()
    return {
        "ok": True,
        "memory_id": str(row.id),
        "status": row.status,
        "applies": (
            "to this person's future runs now; follow it for the rest of this thread too"
            if personal
            else "once the workspace's curator adopts it, never sooner: others write here"
        ),
    }


@expose_mcp()
@audit_tool()
@access(_PERSONAL)
async def forget(memory_id: str) -> dict[str, object]:
    """Implement the `forget` tool."""
    login = await resolve_github_login(as_json_object(get_config()))
    person = await User.for_login("github", login) if login else None
    if person is None:
        return {"ok": False, "error": "Could not tell whose memories to change."}
    try:
        parsed = UUID(memory_id)
    except ValueError:
        return {"ok": False, "error": "memory_id is not a memory id"}
    withdrawal = await MEMORIES.withdraw(parsed, person.id)
    if withdrawal is None:
        return {"ok": False, "error": "No memory you saved has that id."}
    return {
        "ok": True,
        "memory_id": memory_id,
        "status": withdrawal,
        "applies": (
            "at once: the curator never saw it"
            if withdrawal == "deleted"
            else "at once for this person's runs, and the workspace's curator decides what "
            "leaves its prompt"
        ),
    }
