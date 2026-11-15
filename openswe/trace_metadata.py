"""Shared trace-only attribution fields for run dispatch and catalog propagation."""

from enum import StrEnum
from typing import Final


class RunOrigin(StrEnum):
    """The bounded triggering surfaces recorded on root traces, independent of thread source."""

    SLACK = "slack"
    GITHUB = "github"
    DASHBOARD = "dashboard"
    SCHEDULE = "schedule"
    CLAUDE_CODE = "claude_code"
    API = "api"
    INCIDENTS_AGENT = "incidents_agent"
    LINEAR = "linear"
    EVAL = "eval"
    AUTOMATION = "automation"

    @classmethod
    def resolve(
        cls, source: object, *, inherited: object = None, fallback: object = None
    ) -> RunOrigin:
        """Prefer this event's surface, then the cause captured by deferred work."""
        for value in (source, inherited, fallback):
            if not isinstance(value, str):
                continue
            if value == "web":
                return cls.DASHBOARD
            if value.startswith("github_") or value == "deployment":
                return cls.GITHUB
            try:
                return cls(value)
            except ValueError:
                continue
        return cls.AUTOMATION


BILLING_USER_FIELDS: Final = frozenset({"user_id", "billed_robin_user_id", "billed_user_email"})
BILLING_FIELDS: Final = BILLING_USER_FIELDS | {"billing_bucket"}
