"""Shared trace-only attribution fields for run dispatch and catalog propagation."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final


class RunOrigin(StrEnum):
    """What started this run, recorded as ``metadata.origin`` on its root trace.

    ``source`` says where the thread lives and never changes; ``origin`` says what
    woke this particular run. A Slack thread (``source="slack"``) is woken by a
    teammate's reply (``origin="slack"``), a review on its PR (``origin="github"``)
    or Robin's own timer (``origin="automation"``). Work a person deferred, like a
    background task they started, keeps their origin; work Robin schedules itself,
    like a review deadline, is ``automation``.
    """

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


class TraceVisibility(StrEnum):
    """Whether a run's thread is public or private, as ``metadata.visibility`` on its root trace.

    Access policies read it to hide private threads' traces, so anything but an explicitly
    public thread counts as private.
    """

    PUBLIC = "public"
    PRIVATE = "private"

    @classmethod
    def of(cls, thread_metadata: Mapping[str, object]) -> TraceVisibility:
        """A thread naming no visibility is public, as everywhere else that checks it."""
        return (
            cls.PUBLIC if thread_metadata.get("visibility", "public") == "public" else cls.PRIVATE
        )


BILLING_USER_FIELDS: Final = frozenset({"user_id", "billed_robin_user_id", "billed_user_email"})
BILLING_FIELDS: Final = BILLING_USER_FIELDS | {"billing_bucket"}
ATTRIBUTION_FIELD: Final = "ls_attribution_identifier"
