"""Timings and the notification channel shared by the bridge's store and listener."""

CHANNEL = "open_swe_bridge"
"""LISTEN/NOTIFY channel carrying ``<bridge_id>:<request_id>:<event>`` — ids only."""

REQUEST_EVENT = "request"
RESULT_EVENT = "result"
CLOSED_EVENT = "closed"

SANDBOX_ID_PREFIX = "bridge:"
"""What a thread's ``sandbox_id`` starts with when its sandbox is someone's laptop."""

HEARTBEAT_INTERVAL_SECONDS = 20
ALIVE_THRESHOLD_SECONDS = 75

DEFAULT_EXECUTE_TIMEOUT_SECONDS = 300
WAIT_GRACE_SECONDS = 30
"""How long past a command's own timeout a waiter gives the round trip."""

DEFAULT_POLL_WAIT_SECONDS = 25
MAX_POLL_WAIT_SECONDS = 30
DEFAULT_CLAIM_LIMIT = 8
MAX_CLAIM_LIMIT = 32
