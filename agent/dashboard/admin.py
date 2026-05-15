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
