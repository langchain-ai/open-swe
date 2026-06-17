"""Admin email gate driven by the CONFIGURED_ADMINS env var."""

from __future__ import annotations

import os


def _admin_emails() -> frozenset[str]:
    raw = os.environ.get("CONFIGURED_ADMINS", "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def is_admin(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in _admin_emails()


def _observability_emails() -> frozenset[str]:
    raw = os.environ.get("OBSERVABILITY_AUTHORIZED_EMAILS", "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def is_observability_authorized(email: str | None) -> bool:
    """Whether ``email`` may use the team observability tools.

    Admins always qualify; additional non-admin emails can be allow-listed via
    ``OBSERVABILITY_AUTHORIZED_EMAILS``.
    """
    if not email:
        return False
    normalized = email.strip().lower()
    return normalized in _admin_emails() or normalized in _observability_emails()
