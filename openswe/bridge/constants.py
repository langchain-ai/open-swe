"""Timings and the notification channel shared by the bridge's store and listener."""

from typing import Literal

BridgeClient = Literal["cli", "desktop"]
"""The app serving a bridge: the ``oswe`` CLI, or the desktop app's "This Mac" threads."""

CHANNEL = "open_swe_bridge"
"""LISTEN/NOTIFY channel carrying ``<bridge_id>:<request_id>:<event>`` — ids only."""

REQUEST_EVENT = "request"
RESULT_EVENT = "result"
CLOSED_EVENT = "closed"

HANDOFF_FROM_KEY = "sandbox_handoff_from"
"""Thread metadata naming the sandbox whose checkout the next run carries over."""

SANDBOX_ID_PREFIX = "bridge:"
"""What a thread's ``sandbox_id`` starts with when its sandbox is the machine running the CLI."""

HEARTBEAT_INTERVAL_SECONDS = 20
ALIVE_THRESHOLD_SECONDS = 75

DEFAULT_EXECUTE_TIMEOUT_SECONDS = 300
WAIT_GRACE_SECONDS = 30
"""How long past a command's own timeout a waiter gives the round trip."""

DEFAULT_POLL_WAIT_SECONDS = 25
MAX_POLL_WAIT_SECONDS = 30
DEFAULT_CLAIM_LIMIT = 8
MAX_CLAIM_LIMIT = 32
