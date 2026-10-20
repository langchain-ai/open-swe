from openswe.input_messages import InputMessageContext

DASHBOARD_HANDOFF_SENDER_ID = "system:dashboard-handoff"
DASHBOARD_HANDOFF_BODY = (
    "This follow-up was sent from Web. The conversation has moved to Web, so answer in "
    "the dashboard stream with a normal assistant message. Do not call slack_reply "
    "unless a later Slack message explicitly moves the conversation back to Slack, or "
    "an event-subscription turn explicitly directs rollout/deployment results to the "
    "Slack thread where that check was requested. That exception applies only to those results."
)
# Stamped by the proxy, never trusted from a client: another agent thread sent
# this on its person's behalf, so it must not move the conversation to Web.
RELAYED_FROM_KEY = "relayed_from_thread_id"


def dashboard_input_context(sender_id: str, relayed_from: str | None) -> InputMessageContext:
    """The envelope of a dashboard send: typed on Web, or relayed by another agent thread."""
    if relayed_from is None:
        return {"sender_id": sender_id, "surface": "web", "kind": "human"}
    return {
        "sender_id": sender_id,
        "surface": "automation",
        "kind": "human",
        "data": {"relayed_from_thread": relayed_from},
    }
