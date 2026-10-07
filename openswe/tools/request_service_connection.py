from typing import Annotated, Literal

from langgraph.config import get_config
from langgraph.prebuilt import InjectedState

from openswe.middleware.require_user_reply import current_reply_surface
from openswe.run_config import RunConfig
from openswe.slack.blocks import actions, block_payload, button, section
from openswe.slack.tools.reply import slack_reply
from openswe.utils.dashboard_links import dashboard_api_base_url


async def request_service_connection(
    service: Literal["notion"],
    state: Annotated[dict[str, object] | None, InjectedState] = None,
) -> dict[str, object]:
    """Offer a personal connection without starting OAuth or reading credentials."""
    if service != "notion":
        return {"success": False, "error": "Unsupported service"}
    cfg = RunConfig.from_config(get_config())
    surface = current_reply_surface(state) if state is not None else cfg.source
    if surface != "slack":
        return {"success": True}
    url = f"{dashboard_api_base_url()}/dashboard/api/notion/login?source=slack"
    message = (
        "Connect Notion to your Open SWE account. The button opens Notion's external "
        "consent screen; sign in to Open SWE in your browser before clicking. After "
        "consent, we'll send a confirmation to your linked Slack account by DM. "
        "This personal connection is only available in your private "
        "threads; it does not grant access in shared channels."
    )
    result = await slack_reply(
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
    if result.get("success") is not True:
        return {"success": False, "error": result.get("error", "Could not post connection card")}
    return {"success": True}
