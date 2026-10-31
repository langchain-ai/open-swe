"""Tools: keep an engram for the person a thread works for, and offer it to the curator."""

from uuid import UUID

from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_overrides import resolve_github_login
from openswe.engrams.store import ENGRAMS, MAX_ENGRAM_CHARS, UnknownWorkspaceError
from openswe.run_config import RunConfig
from openswe.tools.access import Policy, access, resolve_access, unchanged
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User
from openswe.utils.json_types import as_json_object

# Where only its person writes, an engram may apply to them at once; anywhere else
# someone else's words could reach it, so it waits for the curator instead.
_PERSONAL = Policy(trusted="private", actor="owner", sole=unchanged)
_ANYWHERE = Policy(trusted="anywhere")


@expose_mcp()
@audit_tool()
@access(_ANYWHERE)
async def remember(text: str, share_with_workspace: bool = False) -> dict[str, object]:
    """Implement the `remember` tool."""
    body = text.strip()
    if not body:
        return {"ok": False, "error": "text is empty"}
    if len(body) > MAX_ENGRAM_CHARS:
        return {"ok": False, "error": f"text exceeds {MAX_ENGRAM_CHARS} characters"}
    cfg = RunConfig.from_runtime()
    login = await resolve_github_login(as_json_object(get_config()))
    person = await User.for_login("github", login) if login else None
    run_access = await resolve_access(cfg)
    personal = person is not None and run_access.mode(_PERSONAL) is not None
    try:
        row = await ENGRAMS.save(
            scope="personal" if personal else "workspace",
            body=body,
            person=person.id if person else None,
            workspace=cfg.workspace_slug,
            thread_id=cfg.thread_id,
            from_private_thread=run_access.private,
            shared=share_with_workspace,
        )
    except UnknownWorkspaceError:
        return {"ok": False, "error": "This thread has no workspace to remember it for."}
    return {
        "ok": True,
        "engram_id": str(row.id),
        "status": row.status,
        "applies": (
            "to this person's future runs now; follow it for the rest of this thread too"
            if personal
            else "once the workspace's curator adopts it, never sooner: others write here"
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
async def forget(engram_id: str) -> dict[str, object]:
    """Implement the `forget` tool."""
    login = await resolve_github_login(as_json_object(get_config()))
    person = await User.for_login("github", login) if login else None
    if person is None:
        return {"ok": False, "error": "Could not tell whose engrams to change."}
    try:
        parsed = UUID(engram_id)
    except ValueError:
        return {"ok": False, "error": "engram_id is not an engram id"}
    withdrawal = await ENGRAMS.withdraw(parsed, person.id)
    if withdrawal is None:
        return {"ok": False, "error": "No engram you saved has that id."}
    return {
        "ok": True,
        "engram_id": engram_id,
        "status": withdrawal,
        "applies": (
            "at once: the curator never saw it"
            if withdrawal == "deleted"
            else "at once for this person's runs, and the workspace's curator decides what "
            "leaves its prompt"
        ),
    }
