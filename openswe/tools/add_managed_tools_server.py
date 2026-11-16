from typing import Annotated

from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from pydantic import ValidationError

from openswe.credential_scope import private_credential_login
from openswe.mcp.cards import ConnectCard
from openswe.mcp.managed import CredentialKind, NewServer
from openswe.middleware.require_user_reply import current_reply_surface
from openswe.run_config import RunConfig
from openswe.slack.blocks import Block, actions, block_payload, button, escape, section
from openswe.slack.tools.reply import send_slack_reply
from openswe.utils.dashboard_links import dashboard_thread_url


async def add_managed_tools_server(
    name: str,
    url: str,
    auth: CredentialKind = "oauth",
    header: str = "Authorization",
    state: Annotated[dict[str, object] | None, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> dict[str, object]:
    """Propose adding the person's own MCP server to their managed tools, for them to confirm."""
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
    try:
        proposed = NewServer(name=name, url=url, kind=auth, header=header)
    except ValidationError as exc:
        return {"success": False, "error": str(exc)}
    if not proposed.url.strip().lower().startswith("https://"):
        return {"success": False, "error": "The server URL must start with https://"}
    card = ConnectCard(
        thread_id=cfg.thread_id, card_id=tool_call_id, login=login, proposed=proposed
    )
    await card.save()
    surface = current_reply_surface(state) if state is not None else cfg.source
    if surface == "slack":
        # A Slack call ends the turn, so the person must hear something; the card they
        # confirm on is in the dashboard thread, behind their Robin sign-in.
        blocks: list[Block] = [
            section(
                f"I can add the MCP server *{escape(proposed.name)}* (`{escape(proposed.url)}`) "
                "to your own "
                "managed tools. Review and confirm it in Robin; nothing is added until you do."
            ),
            actions(
                button(
                    "Review in Robin",
                    action_id="review_managed_tools_server",
                    url=dashboard_thread_url(card.thread_id) or "",
                )
            ),
        ]
        posted = await send_slack_reply(
            "Confirm adding an MCP server", "final", blocks=block_payload(blocks), state=state
        )
        if posted.get("success") is not True:
            return {"success": False, "error": posted.get("error", "Could not reply")}
    return {
        "success": True,
        "status": "confirmation_required",
        "server": {"name": proposed.name, "url": proposed.url, "kind": proposed.kind},
    }
