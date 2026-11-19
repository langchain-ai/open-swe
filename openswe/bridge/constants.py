"""Timings and the notification channel shared by the bridge's store and listener."""

CHANNEL = "open_swe_bridge"
"""LISTEN/NOTIFY channel carrying ``<bridge_id>:<request_id>:<event>`` — ids only."""

REQUEST_EVENT = "request"
RESULT_EVENT = "result"
CLOSED_EVENT = "closed"

LOCAL_ACCESS_CLIENT = "desktop"
LOCAL_ACCESS_ROOT = "local-access://approved-actions"
"""What the desktop app opens its approved-actions bridge with: the only bridge there is."""

HEARTBEAT_INTERVAL_SECONDS = 20
ALIVE_THRESHOLD_SECONDS = 75

WAIT_GRACE_SECONDS = 30
"""How long past an action's own timeout a waiter gives the round trip."""

DEFAULT_POLL_WAIT_SECONDS = 25
MAX_POLL_WAIT_SECONDS = 30
DEFAULT_CLAIM_LIMIT = 8
MAX_CLAIM_LIMIT = 32
