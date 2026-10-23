"""Tools linking a person's concierge DM agent and its background thread."""

from openswe.concierge.background import DEFAULT_INSTRUCTIONS, Concierge
from openswe.concierge.records import ConciergeBackground
from openswe.run_config import RunConfig
from openswe.slack.thread_owner import ThreadOwnerWakeError
from openswe.tools.access import Policy, access
from openswe.users import User

_MAX_INSTRUCTIONS_CHARS = 8_000
_MAX_REPORT_CHARS = 8_000
_READ = Policy(trusted="private", actor="owner")
# Only the person's own message may change what the background thread does unasked.
_WRITE = Policy(trusted="private", actor="owner", direct=True)


@access(_READ, per_call=lambda args: _READ if args.get("instructions") is None else _WRITE)
async def concierge_background(instructions: str | None = None) -> dict[str, object]:
    """Implement the `concierge_background` tool."""
    cfg = RunConfig.from_runtime()
    slack_user_id = cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
    user = await User.for_identity("slack", slack_user_id) if slack_user_id else None
    if user is None:
        return {"ok": False, "error": "Could not resolve the person this DM belongs to"}
    if instructions is not None and len(instructions) > _MAX_INSTRUCTIONS_CHARS:
        return {
            "ok": False,
            "error": f"instructions must be at most {_MAX_INSTRUCTIONS_CHARS} characters",
        }
    row = (
        await ConciergeBackground.save_instructions(user.id, instructions.strip())
        if instructions is not None
        else await ConciergeBackground.for_user(user.id)
    )
    custom = row.instructions if row is not None else ""
    return {
        "ok": True,
        "thread_id": row.thread_id if row is not None else None,
        "instructions": custom or DEFAULT_INSTRUCTIONS,
        "custom_instructions": bool(custom),
        "timezone": user.slack_timezone or "unknown",
    }


async def tell_concierge(message: str, wake: bool = True) -> dict[str, object]:
    """Implement the `tell_concierge` tool."""
    if not message.strip():
        return {"ok": False, "error": "message is required"}
    if len(message) > _MAX_REPORT_CHARS:
        return {"ok": False, "error": f"message must be at most {_MAX_REPORT_CHARS} characters"}
    thread_id = RunConfig.from_runtime().thread_id
    concierge = await Concierge.for_background_thread(thread_id) if thread_id else None
    if concierge is None:
        return {"ok": False, "error": "This thread is not a concierge background thread"}
    try:
        await concierge.tell(message.strip(), wake=wake)
    except ThreadOwnerWakeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "woke": wake}
