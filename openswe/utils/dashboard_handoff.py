from collections.abc import Mapping, Sequence

from openswe.attachments import envelope_data
from openswe.input_messages import FieldValue, InputMessageContext

DASHBOARD_HANDOFF_SENDER_ID = "system:dashboard-handoff"
DASHBOARD_HANDOFF_BODY = (
    "This follow-up was sent from Web. The conversation has moved to Web, so answer in "
    "the dashboard stream with a normal assistant message. Do not call slack_reply "
    "unless a later Slack message explicitly moves the conversation back to Slack."
)
# Stamped by the proxy, never trusted from a client: another agent thread sent
# this on its person's behalf, so it must not move the conversation to Web.
RELAYED_FROM_KEY = "relayed_from_thread_id"


def dashboard_input_context(
    sender_id: str,
    relayed_from: str | None,
    attachments: Sequence[Mapping[str, FieldValue | None]] = (),
) -> InputMessageContext:
    """The envelope of a dashboard send: typed on Web, or relayed by another agent thread.

    ``attachments`` are the fields of each file the send placed in the sandbox.
    """
    data: dict[str, FieldValue | Mapping[str, FieldValue | None]] = {}
    if relayed_from is not None:
        data["relayed_from_thread"] = relayed_from
    data.update(envelope_data(attachments))
    context: InputMessageContext = {
        "sender_id": sender_id,
        "surface": "web" if relayed_from is None else "automation",
        "kind": "human",
    }
    if data:
        context["data"] = data
    return context
