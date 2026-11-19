"""Read global model defaults privately or change them through authorized admin writes."""

from typing import Literal

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.settings import Settings, get_instance_settings
from openswe.tools.access import Policy, access, ack
from openswe.tools.mcp_exposure import expose_mcp

type ModelDefaultRole = Literal[
    "agent",
    "agent_subagent",
    "thread_title",
    "reviewer",
    "reviewer_subagent",
    "review_scout",
]

_READ = Policy(trusted="admin_surface", actor="admin")
_WRITE = Policy(trusted="admin_surface", actor="admin", sole=ack("scope"))

_ROLE_FIELDS: dict[ModelDefaultRole, tuple[str, str]] = {
    "agent": ("default_agent_model", "default_agent_reasoning_effort"),
    "agent_subagent": ("default_agent_subagent_model", "default_agent_subagent_reasoning_effort"),
    "thread_title": ("default_thread_title_model", "default_thread_title_reasoning_effort"),
    "reviewer": ("default_reviewer_model", "default_reviewer_reasoning_effort"),
    "reviewer_subagent": (
        "default_reviewer_subagent_model",
        "default_reviewer_subagent_reasoning_effort",
    ),
    "review_scout": ("default_review_scout_model", "default_review_scout_reasoning_effort"),
}


@audit_tool(reads={"read"})
@expose_mcp(access="admin")
@access(_WRITE, per_call=lambda args: _WRITE if args.get("action") == "set" else _READ)
async def manage_model_defaults(
    action: Literal["read", "set"],
    role: ModelDefaultRole | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    """Read or set global default models and reasoning efforts for Robin roles."""
    if action == "set":
        if role is None:
            raise ValueError("Provide a role to set")
        model_field, effort_field = _ROLE_FIELDS[role]
        await Settings.patch({model_field: model, effort_field: reasoning_effort})
    elif role is not None or model is not None or reasoning_effort is not None:
        raise ValueError("Omit role, model, and reasoning_effort when reading")

    effective = await get_instance_settings()
    return {
        "scope": "instance",
        "effective": {
            name: {
                "model": effective.get(model_field),
                "reasoning_effort": effective.get(effort_field),
            }
            for name, (model_field, effort_field) in _ROLE_FIELDS.items()
        },
    }
