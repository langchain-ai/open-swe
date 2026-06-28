DASHBOARD_HANDOFF_MARKER = "[Open SWE Web handoff]"
DASHBOARD_HANDOFF_INSTRUCTION = (
    f"{DASHBOARD_HANDOFF_MARKER} This follow-up was sent from Web. "
    "The conversation has moved to Web, so answer in the dashboard stream with a normal "
    "assistant message. Do not call slack_thread_reply unless a later Slack message explicitly "
    "moves the conversation back to Slack."
)
