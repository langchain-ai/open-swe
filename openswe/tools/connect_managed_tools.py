from typing import Annotated
from urllib.parse import urlencode

from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from openswe.credential_scope import private_credential_login
from openswe.mcp.cards import ConnectCard
from openswe.mcp.managed import (
    Gateway,
    GatewayStatus,
    LangSmithNotConnected,
    ManagedToolsError,
    MissingCredential,
    gateway_id,
    gateway_status,
    instance_gateway,
)
from openswe.middleware.require_user_reply import current_reply_surface
from openswe.run_config import RunConfig
from openswe.slack.blocks import Block, actions, block_payload, button, context, section
from openswe.slack.tools.reply import send_slack_reply
from openswe.utils.dashboard_links import dashboard_api_base_url, dashboard_thread_url


def _langsmith_blocks(card: ConnectCard) -> list[Block]:
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


def _slack_blocks(
    card: ConnectCard, gateway: Gateway, offered: list[MissingCredential]
) -> list[Block]:
    oauth = [item for item in offered if item.kind == "oauth"]
    secret = [item for item in offered if item.kind == "secret"]
    then = (
        "I'll continue here once they're connected."
        if not secret
        else "Once they're set up, ask me to continue."
    )
    blocks: list[Block] = [
        section(
            f"Connect these services to use them through the *{gateway.name}* managed tools. "
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


def _nothing_to_connect(status: GatewayStatus, services: list[str]) -> tuple[str, str]:
    """What to tell the model, and a Slack reply, when no requested service is unconnected."""
    if status.ready:
        return (
            "Every service is connected; the tools load on the person's next message",
            f"Every service in the *{status.gateway.name}* managed tools is already "
            "connected; they load on your next message.",
        )
    names = ", ".join(item.display_name for item in status.missing)
    asked = ", ".join(services)
    return (
        f"None of {asked} is waiting to be connected: each is connected already (its tools "
        f"load on the person's next message) or is not in this tool kit. Unconnected: {names}",
        f"{asked} isn't waiting to be connected in the *{status.gateway.name}* managed tools; "
        f"it's either connected already or not one of them. Not connected yet: {names}.",
    )


async def connect_managed_tools(
    services: list[str] | None = None,
    state: Annotated[dict[str, object] | None, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> dict[str, object]:
    """Offer cards to connect services in the instance's managed tools."""
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
    configured = await instance_gateway()
    if configured is None:
        return {"success": False, "error": "No managed tools are set up on this Open SWE"}
    gateway = gateway_id(configured)
    try:
        status = await gateway_status(login, gateway)
    except LangSmithNotConnected:
        status = None
    except ManagedToolsError as exc:
        return {"success": False, "error": str(exc)}
    surface = current_reply_surface(state) if state is not None else cfg.source

    async def post(text: str, blocks: list[Block] | None = None) -> dict[str, object] | None:
        # A Slack call ends the turn, so the person must hear something.
        if surface != "slack":
            return None
        posted = await send_slack_reply(
            text, "final", blocks=block_payload(blocks) if blocks else None, state=state
        )
        if posted.get("success") is not True:
            return {"success": False, "error": posted.get("error", "Could not reply")}
        return None

    if status is None:
        card = ConnectCard(
            thread_id=cfg.thread_id, card_id=tool_call_id, login=login, gateway=gateway
        )
        if failed := await post("Connect LangSmith", _langsmith_blocks(card)):
            return failed
        return {"success": True, "status": "langsmith_required"}

    offered = status.missing_for(services or [])
    if not offered:
        message, reply = _nothing_to_connect(status, services or [])
        if failed := await post(reply):
            return failed
        return {
            "success": True,
            "status": "connected" if status.ready else "nothing_to_connect",
            "gateway": status.gateway.model_dump(),
            "message": message,
        }
    card = ConnectCard(
        thread_id=cfg.thread_id,
        card_id=tool_call_id,
        login=login,
        gateway=gateway,
        services=[item.slug for item in offered],
    )
    await card.save()
    if failed := await post(
        "Connect your managed tools", _slack_blocks(card, status.gateway, offered)
    ):
        return failed
    return {
        "success": True,
        "status": "connection_required",
        "gateway": status.gateway.model_dump(),
        "missing": [item.model_dump() for item in offered],
        "all_required": status.all_required,
        "continues_automatically": all(item.kind == "oauth" for item in offered),
    }
