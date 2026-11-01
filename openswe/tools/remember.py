"""Tools: keep what a thread learned, for its person or for the workspace's curator."""

from typing import Literal
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

_PERSONAL = Policy(trusted="private", actor="owner", sole=unchanged)
_WORKSPACE = Policy(trusted="anywhere")


@expose_mcp()
# A workspace memory changes nothing until the curator adopts it, and the prompt it
# then publishes is audited; only a personal one changes how a person's runs go.
@audit_tool(reads={"workspace"}, read_key="scope")
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
    login = await resolve_github_login(as_json_object(get_config()))
    person = await User.for_login("github", login) if login else None
    if scope == "personal" and person is None:
        return {
            "ok": False,
            "error": "Could not tell whose memory this is; personal memories need a person.",
        }
    try:
        row = await MEMORIES.save(
            scope=scope,
            body=body,
            person=person.id if person else None,
            workspace=cfg.workspace_slug,
            thread_id=cfg.thread_id,
            from_private_thread=(await resolve_access(cfg)).private,
            shared=share_with_workspace,
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
            if scope == "personal"
            else "to this person's runs in this workspace as if personal until the curator "
            "processes it, and to everyone's once it adopts it"
            if row.from_private_thread
            and person is not None
            and person.typed_preferences.experimental_memory
            else "once the workspace's curator adopts it, never sooner"
        ),
        **(
            {"held": "Saved in a private thread and not shared: the curator will never see it."}
            if row.status == "held"
            else {}
        ),
    }


@expose_mcp()
# Withdrawing a workspace memory only asks the curator, like remembering one.
@audit_tool(reads={"workspace"}, read_key="scope")
@access(
    _WORKSPACE, per_call=lambda args: _PERSONAL if args.get("scope") == "personal" else _WORKSPACE
)
async def forget(memory_id: str, scope: Literal["personal", "workspace"]) -> dict[str, object]:
    """Implement the `forget` tool."""
    login = await resolve_github_login(as_json_object(get_config()))
    person = await User.for_login("github", login) if login else None
    if person is None:
        return {"ok": False, "error": "Could not tell whose memories to change."}
    try:
        parsed = UUID(memory_id)
    except ValueError:
        return {"ok": False, "error": "memory_id is not a memory id"}
    withdrawal = await MEMORIES.withdraw(parsed, person.id, scope)
    if withdrawal is None:
        return {"ok": False, "error": f"No {scope} memory you saved has that id."}
    return {
        "ok": True,
        "memory_id": memory_id,
        "status": withdrawal,
        "applies": (
            "at once: the curator never saw it"
            if withdrawal == "deleted"
            else "at once for this person's runs, and the workspace's curator decides what "
            "leaves its prompt"
            if scope == "personal"
            else "once the workspace's curator decides it, even if it already adopted it"
        ),
    }
