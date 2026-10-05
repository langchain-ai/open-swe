from typing import Annotated, Literal
from urllib.parse import urlencode

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from agent.middleware.require_user_reply import current_reply_surface
from agent.run_config import RunConfig
from agent.slack.blocks import actions, block_payload, button, section
from agent.slack.tools.reply import slack_reply
from agent.tools.errors import ToolError
from agent.utils.dashboard_links import dashboard_api_base_url, dashboard_thread_url


async def request_service_connection(
    service: Literal["notion"],
    state: Annotated[dict[str, object] | None, InjectedState] = None,
) -> dict[str, object]:
    """Offer a personal connection without starting OAuth or reading credentials."""
    if service != "notion":
        raise ToolError("Unsupported service")
    cfg = RunConfig.from_config(get_config())
    surface = current_reply_surface(state) if state is not None else cfg.source
    if surface != "slack":
        return {"success": True}
    target = dashboard_thread_url(cfg.thread_id or "")
    if target is None:
        raise ToolError("Dashboard thread URL unavailable")
    url = f"{dashboard_api_base_url()}/dashboard/api/notion/login?" + urlencode(
        {"redirect_to": target}
    )
    message = (
        "Connect Notion to your Open SWE account. The button opens Notion's external "
        "consent screen; sign in to Open SWE in your browser before clicking. After "
        "consent, it returns you to this "
        "chat on the web. This personal connection is only available in your private "
        "threads; it does not grant access in shared channels."
    )
    await slack_reply(
        message,
        "final",
        blocks=block_payload(
            [
                section(message),
                actions(button("Connect Notion", action_id="connect_notion", url=url)),
            ]
        ),
        state=state,
    )
    return {"success": True}
