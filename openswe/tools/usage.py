"""Audience-safe usage and recorded spend reporting."""

from typing import Literal
from uuid import UUID

from openswe.analytics.gateway import GatewaySpend
from openswe.analytics.identity import opaque_person
from openswe.analytics.queries import usage_leaderboard
from openswe.dashboard.principal import DashboardPrincipal
from openswe.tools.access import Policy, access, resolve_access
from openswe.tools.admin_gate import actor_is_admin, configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User


@expose_mcp()
@access(Policy(trusted="private", actor="owner"))
async def read_usage(
    period: Literal["24h", "7d", "30d", "mtd", "all"] = "30d",
    limit: int = 100,
    cursor: str | None = None,
    user_id: UUID | None = None,
    personal_only: bool = False,
    include_gateway_spend: bool = True,
) -> dict[str, object]:
    """Read recorded Robin usage and spend; admins may read everyone, others only themselves."""
    cfg = configurable()
    person = await User.for_login(
        "github", cfg.mcp_caller or (cfg.github_login if cfg.github_linked else cfg.sender_id) or ""
    )
    if person is None:
        raise ValueError("An authenticated Robin user is required")
    admin = await actor_is_admin(cfg)
    if user_id is not None and not admin and user_id != person.id:
        raise ValueError("Only admins may read another person's usage")
    if admin and not personal_only:
        if (await resolve_access()).mode(Policy(trusted="admin_surface", actor="admin")) is None:
            raise ValueError("Use a private admin dashboard thread or authenticated admin Slack DM")
        selected_id = user_id
    else:
        if user_id is not None and user_id != person.id:
            raise ValueError("personal_only reads only the caller's usage")
        selected_id = person.id
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    analytics_id = None
    if selected_id is not None:
        selected = person if selected_id == person.id else await User.get(selected_id)
        if selected is None:
            raise ValueError("The selected Robin user does not exist")
        analytics_id = opaque_person("github", selected.github_id)
        if analytics_id is None:
            raise ValueError("A verified immutable GitHub identity is required for personal usage")
    result = await usage_leaderboard(
        period=period,
        limit=limit,
        cursor=cursor,
        current_login=person.github_login,
        current_email=person.email,
        admin=admin,
        own_user_id=analytics_id,
    )
    gateway: dict[str, object] = {"status": "not_requested"}
    if include_gateway_spend:
        if selected_id == person.id:
            principal = DashboardPrincipal(
                "github", f"user:{person.id}", person.id, resolved_user=person
            )
            gateway = (await GatewaySpend.for_person(principal)).model_dump(mode="json")
        else:
            gateway = {
                "status": "unsupported_scope",
                "note": "Live gateway counters are available only for the caller; use personal_only=true. Other users and deployment-wide spend use recorded Robin costs.",
            }
    return {
        "gateway_spend": gateway,
        "cost_note": "Recorded Robin costs may be incomplete and exclude external gateway calls; gateway counters use their own policy windows, not the selected reporting period.",
        "scope": "all_users" if selected_id is None else "personal",
        "source": "recorded_robin_usage",
        **result,
    }
