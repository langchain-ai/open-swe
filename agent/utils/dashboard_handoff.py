DASHBOARD_HANDOFF_OPEN_TAG = "<open_swe_web_handoff>"
DASHBOARD_HANDOFF_CLOSE_TAG = "</open_swe_web_handoff>"
DASHBOARD_HANDOFF_MARKER = DASHBOARD_HANDOFF_OPEN_TAG
DASHBOARD_HANDOFF_BODY = (
    "This follow-up was sent from Web. The conversation has moved to Web, so answer in "
    "the dashboard stream with a normal assistant message. Do not call slack_thread_reply "
    "unless a later Slack message explicitly moves the conversation back to Slack."
)
DASHBOARD_HANDOFF_INSTRUCTION = (
    f"{DASHBOARD_HANDOFF_OPEN_TAG}\n{DASHBOARD_HANDOFF_BODY}\n{DASHBOARD_HANDOFF_CLOSE_TAG}"
)
