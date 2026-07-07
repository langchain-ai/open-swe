"""Admin gate driven by the CONFIGURED_ADMINS env var."""

from __future__ import annotations

import os


def _configured_admins() -> frozenset[str]:
    raw = os.environ.get("CONFIGURED_ADMINS", "")
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def _observability_emails() -> frozenset[str]:
    raw = os.environ.get("OBSERVABILITY_AUTHORIZED_EMAILS", "")
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def _admin_identities(email: str | None, login: str | None) -> frozenset[str]:
    return frozenset(
        value.strip().lower()
        for value in (email, login)
        if isinstance(value, str) and value.strip()
    )


def is_admin(email: str | None, *, login: str | None = None) -> bool:
    return bool(_admin_identities(email, login) & _configured_admins())


def is_observability_authorized(email: str | None, *, login: str | None = None) -> bool:
    """Whether a user may use the team observability tools."""
    identities = _admin_identities(email, login)
    if identities & _configured_admins():
        return True
    if not email:
        return False
    return email.strip().lower() in _observability_emails()
