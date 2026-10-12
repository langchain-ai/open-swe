from typing import Annotated
from urllib.parse import urlencode

from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from openswe.credential_scope import private_credential_login
from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.mcp.cards import ConnectCard
from openswe.mcp.managed import (
    GatewayStatus,
    LangSmithNotConnected,
    ManagedToolsError,
    gateway_id,
    gateway_status,
)
from openswe.middleware.require_user_reply import current_reply_surface
from openswe.run_config import RunConfig
from openswe.slack.blocks import Block, actions, block_payload, button, context, section
from openswe.slack.tools.reply import slack_reply
from openswe.utils.dashboard_links import dashboard_api_base_url, dashboard_thread_url
from openswe.workspaces.store import DEFAULT_WORKSPACE_SLUG


def _slack_blocks(card: ConnectCard, status: GatewayStatus | None) -> list[Block]:
    if status is None:
        target = dashboard_thread_url(card.thread_id) or ""
        url = f"{dashboard_api_base_url()}/dashboard/api/langsmith/login?" + urlencode(
            {"redirect_to": target}
        )
        return [
            section(
                "Managed tools run with your own LangSmith account. Connect LangSmith, "
                "then ask me again to connect the services they need."
            ),
            actions(button("Connect LangSmith", action_id="connect_langsmith", url=url)),
        ]
    oauth = [item for item in status.missing if item.kind == "oauth"]
    secret = [item for item in status.missing if item.kind == "secret"]
    then = (
        "I'll continue here once all are connected."
        if status.consent_only
        else "Once every service is set up, ask me to continue."
    )
    blocks: list[Block] = [
        section(
            f"Connect these services to use the *{status.gateway.name}* managed tools. "
            "Each button opens the service's consent screen for your account only; sign "
            f"in to Open SWE in your browser first. {then}"
        )
    ]
    if oauth:
        blocks.append(
            actions(
                *(
                    button(
                        f"Connect {item.display_name}",
                        action_id=f"connect_managed_tool:{index}",
                        url=card.button_url(item.slug),
                    )
                    for index, item in enumerate(oauth[:25])
                )
            )
        )
    if secret:
        names = ", ".join(item.display_name for item in secret)
        blocks.append(context(f"Set the API key for {names} in LangSmith."))
    return blocks


async def connect_managed_tools(
    state: Annotated[dict[str, object] | None, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> dict[str, object]:
    """Offer cards to connect the services in this workspace's managed tools."""
    cfg = RunConfig.from_config(get_config())
    try:
        login = await private_credential_login()
    except RuntimeError as exc:
        return {"success": False, "error": str(exc)}
    if login is None or not cfg.thread_id:
        return {
            "success": False,
            "error": "Managed tools only load in private threads; suggest continuing privately",
        }
    settings = await cached_workspace_settings(cfg.workspace_slug or DEFAULT_WORKSPACE_SLUG)
    if settings.managed_tools_gateway_id is None:
        return {"success": False, "error": "This workspace has no managed tools"}
    card = ConnectCard(
        thread_id=cfg.thread_id,
        card_id=tool_call_id,
        login=login,
        gateway=gateway_id(settings.managed_tools_gateway_id),
    )
    status: GatewayStatus | None
    try:
        status = await gateway_status(login, card.gateway, [])
    except LangSmithNotConnected:
        status = None
    except ManagedToolsError as exc:
        return {"success": False, "error": str(exc)}
    surface = current_reply_surface(state) if state is not None else cfg.source
    if status is not None and status.ready:
        if surface == "slack":
            # A Slack call ends the turn, so the person must hear something.
            posted = await slack_reply(
                f"Every service in the *{status.gateway.name}* managed tools is already "
                "connected; they load on your next message.",
                "final",
                state=state,
            )
            if posted.get("success") is not True:
                return {"success": False, "error": posted.get("error", "Could not reply")}
        return {
            "success": True,
            "status": "connected",
            "gateway": status.gateway.model_dump(),
            "message": "Every service is connected; the tools load on the person's next message",
        }
    if status is not None:
        await card.save()
    if surface == "slack":
        posted = await slack_reply(
            "Connect your managed tools",
            "final",
            blocks=block_payload(_slack_blocks(card, status)),
            state=state,
        )
        if posted.get("success") is not True:
            return {"success": False, "error": posted.get("error", "Could not post the card")}
    if status is None:
        return {"success": True, "status": "langsmith_required"}
    return {
        "success": True,
        "status": "connection_required",
        "gateway": status.gateway.model_dump(),
        "missing": [item.model_dump() for item in status.missing],
        "continues_automatically": status.consent_only,
    }
