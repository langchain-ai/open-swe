"""Agent tools that start slacklets in the run's own Slack thread and change the ones it defined."""

from typing import Any
from uuid import UUID

from langgraph.config import get_config
from pydantic import ValidationError

from openswe.audit_logs.tools import audit_tool
from openswe.run_config import RunConfig
from openswe.slack.cards import run_slack_location
from openswe.slack.http import SlackRequestError
from openswe.slacklets.dynamic import (
    DYNAMIC_KIND,
    ProgramCommand,
    SlackletDefinition,
    SlackletProgramError,
)
from openswe.slacklets.kind import SlackletRefused
from openswe.slacklets.kinds import KINDS
from openswe.slacklets.store import Slacklet, transcript
from openswe.users import User
from openswe.utils.json_types import JsonObject

_NO_THREAD = {"success": False, "error": "This run has no Slack thread to post the slacklet in"}


async def _run() -> tuple[RunConfig, tuple[str, str]]:
    cfg = RunConfig.from_config(get_config())
    return cfg, await run_slack_location(cfg, cfg.thread_id or "")


async def _creator(cfg: RunConfig) -> UUID | None:
    user = await User.for_login("github", cfg.github_login) if cfg.github_login else None
    return user.id if user else None


@audit_tool()
async def start_slacklet(kind: str, subject: str) -> dict[str, Any]:
    """Implement the `start_slacklet` tool."""
    slacklet_kind = KINDS.get(kind)
    if slacklet_kind is None:
        return {"success": False, "error": f"kind must be one of: {', '.join(sorted(KINDS))}"}
    cfg, location = await _run()
    if not all(location):
        return _NO_THREAD
    try:
        slacklet, entries = await slacklet_kind.begin(
            subject, cfg, location, created_by=await _creator(cfg)
        )
    except SlackletRefused as exc:
        return {"success": False, "error": str(exc)}
    except SlackRequestError as exc:
        return {"success": False, "error": f"Slack refused the slacklet message: {exc.code}"}
    return {
        "success": True,
        "slacklet_id": str(slacklet.id),
        "message_ts": slacklet.message_ts,
        "commands": transcript(entries),
    }


@audit_tool()
async def create_slacklet(
    name: str, program: str, params: JsonObject | None = None
) -> dict[str, Any]:
    """Implement the `create_slacklet` tool."""
    cfg, location = await _run()
    if not all(location):
        return _NO_THREAD
    first = ProgramCommand(op="init", params=params or {})
    draft = SlackletDefinition(
        name=name.strip()[:80] or "Slacklet", source=program, source_sha256=""
    )
    try:
        draft.kind().apply(None, first)
    except SlackletProgramError as exc:
        return {"success": False, "error": str(exc)}
    creator = await _creator(cfg)
    definition = await SlackletDefinition.save(draft.name, program, created_by=creator)
    try:
        slacklet, entries = await Slacklet.attach(
            definition.kind(), str(definition.id), location, first, created_by=creator
        )
    except SlackRequestError as exc:
        return {"success": False, "error": f"Slack refused the slacklet message: {exc.code}"}
    return {
        "success": True,
        "slacklet_id": str(slacklet.id),
        "message_ts": slacklet.message_ts,
        "commands": transcript(entries),
    }


@audit_tool()
async def update_slacklet(
    slacklet_id: str, op: str, params: JsonObject | None = None, note: str = ""
) -> dict[str, Any]:
    """Implement the `update_slacklet` tool."""
    cfg, location = await _run()
    try:
        slacklet = await Slacklet.get(UUID(slacklet_id))
    except ValueError:
        slacklet = None
    if slacklet is None or slacklet.location != location:
        return {"success": False, "error": "No slacklet with that id in this Slack thread"}
    if slacklet.kind != DYNAMIC_KIND:
        return {
            "success": False,
            "error": f"A {slacklet.kind} slacklet changes on its own; only ones you created "
            "with create_slacklet take commands",
        }
    definition = await SlackletDefinition.get(UUID(slacklet.subject))
    if definition is None:
        return {"success": False, "error": "The slacklet's program is gone"}
    try:
        command = ProgramCommand(op=op, params=params or {}, note=note)
        applied = await slacklet.advance(definition.kind(), lambda _state: [command])
    except (SlackletProgramError, ValidationError) as exc:
        return {"success": False, "error": f"The command was refused and not applied: {exc}"}
    return {"success": True, "commands": transcript(applied)}
