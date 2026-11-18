from typing import Annotated
from urllib.parse import urlencode

from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from openswe.credential_scope import private_credential_login
from openswe.mcp.cards import ConnectCard
from openswe.mcp.managed import (
    LangSmithNotConnected,
    ManagedToolsError,
    PersonalTools,
)
from openswe.middleware.require_user_reply import current_reply_surface
from openswe.run_config import RunConfig
from openswe.slack.blocks import Block, actions, block_payload, button, section
from openswe.slack.tools.reply import send_slack_reply
from openswe.utils.dashboard_links import dashboard_api_base_url, dashboard_thread_url


def _langsmith_blocks(card: ConnectCard) -> list[Block]:
    target = dashboard_thread_url(card.thread_id) or ""
    url = f"{dashboard_api_base_url()}/dashboard/api/auth/login?" + urlencode(
        {"redirect_to": target}
    )
    return [
        section(
            "Managed tools run with your LangSmith sign-in, which expired. Sign in again, "
            "then ask me again to connect the services they need."
        ),
        actions(button("Sign in with LangSmith", action_id="connect_langsmith", url=url)),
    ]


async def connect_managed_tools(
    services: list[str] | None = None,
    state: Annotated[dict[str, object] | None, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> dict[str, object]:
    """Offer cards to connect the person's own accounts to LangSmith Managed Tools services."""
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
    tools = PersonalTools(login)
    try:
        offered = await tools.unconnected(services or [])
    except LangSmithNotConnected:
        offered = None
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
        channel_id, message_ts = posted.get("channel_id"), posted.get("message_ts")
        if card.services and isinstance(channel_id, str) and isinstance(message_ts, str):
            card.slack_channel_id, card.slack_message_ts = channel_id, message_ts
            footer = posted.get("footer")
            card.slack_footer = footer if isinstance(footer, str) else None
            await card.save()
        return None

    card = ConnectCard(thread_id=cfg.thread_id, card_id=tool_call_id, login=login)
    if offered is None:
        if failed := await post("Sign in with LangSmith", _langsmith_blocks(card)):
            return failed
        return {"success": True, "status": "langsmith_required"}
    if not offered:
        asked = ", ".join(services or []) or "Nothing"
        if failed := await post(
            f"{asked} isn't waiting to be connected: it's connected already (its tools load "
            "on your next message) or LangSmith has no such service."
        ):
            return failed
        return {
            "success": True,
            "status": "nothing_to_connect",
            "message": f"None of {asked} is waiting to be connected: each is connected already "
            "(its tools load on the person's next message) or not a LangSmith service",
        }
    card.services = [server.id for server in offered]
    await card.save()
    if failed := await post("Connect your managed tools", card.slack_blocks(offered)):
        return failed
    return {
        "success": True,
        "status": "connection_required",
        "missing": [
            {"id": server.id, "name": server.name, "kind": server.kind} for server in offered
        ],
        "continues_automatically": all(server.kind == "oauth" for server in offered),
    }
